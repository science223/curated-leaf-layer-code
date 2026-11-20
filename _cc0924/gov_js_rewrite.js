#!/usr/bin/env node
// AST helper for gov harvest. Prefers @babel/parser; falls back to acorn
// (mineflayer node_modules). No network, no package install.
"use strict";

const fs = require("fs");
const path = require("path");

function abortProbe(candId) {
  const id = String(candId || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return "if (globalThis.__govActive !== \"" + id + "\") return;";
}

function tryLoadParser() {
  const roots = [
    path.join(__dirname, "..", "voyager", "utils", "node_modules"),
    path.join(__dirname, "..", "voyager", "env", "mineflayer", "node_modules"),
  ];
  for (const root of roots) {
    try {
      const parser = require(path.join(root, "@babel", "parser"));
      return { kind: "babel", parse: (code) => parser.parse(code, {
        sourceType: "script",
        ranges: true,
        plugins: ["classProperties"],
      }) };
    } catch (e) {}
  }
  for (const root of roots) {
    try {
      const acorn = require(path.join(root, "acorn"));
      return { kind: "acorn", parse: (code) => acorn.parse(code, {
        ecmaVersion: 2022,
        sourceType: "script",
        ranges: true,
        locations: true,
      }) };
    } catch (e) {}
  }
  return null;
}

function _chatStaticPrefix(arg) {
  // leftmost literal text of a chat argument, or null when the prefix is dynamic
  if (!arg) return null;
  if (arg.type === "Literal" && typeof arg.value === "string") return arg.value;
  if (arg.type === "StringLiteral" && typeof arg.value === "string") return arg.value;
  if (arg.type === "TemplateLiteral") {
    if (arg.expressions && arg.expressions.length) {
      const q = arg.quasis && arg.quasis[0];
      return q ? String((q.value && (q.value.cooked != null ? q.value.cooked : q.value.raw)) || "") : null;
    }
    const q = arg.quasis && arg.quasis[0];
    return q ? String((q.value && (q.value.cooked != null ? q.value.cooked : q.value.raw)) || "") : "";
  }
  if (arg.type === "BinaryExpression" && arg.operator === "+") return _chatStaticPrefix(arg.left);
  return null;
}

function memberPropName(node) {
  if (!node || node.type !== "MemberExpression") return null;
  const prop = node.property;
  if (!node.computed && prop && prop.type === "Identifier") return prop.name;
  const lit = stringLiteralValue(prop);
  if (lit != null) return lit;
  return null;
}

function isChatMember(node) {
  return memberPropName(node) === "chat";
}

function isTossMember(node) {
  return memberPropName(node) === "toss";
}

function walk(node, fn, parent) {
  if (!node || typeof node !== "object") return;
  if (node.type) fn(node, parent);
  for (const k of Object.keys(node)) {
    if (k === "start" || k === "end" || k === "loc" || k === "range" || k === "type") continue;
    const v = node[k];
    if (Array.isArray(v)) {
      for (const x of v) walk(x, fn, node);
    } else if (v && typeof v === "object" && v.type) {
      walk(v, fn, node);
    }
  }
}

function nodeName(node) {
  if (!node) return null;
  if (node.type === "Identifier") return node.name;
  if (node.type === "MemberExpression" && !node.computed) {
    const obj = nodeName(node.object);
    const prop = node.property && node.property.name;
    if (obj && prop) return obj + "." + prop;
  }
  return null;
}

function applyPatches(code, patches) {
  patches = patches.filter(Boolean).sort((a, b) => b.start - a.start);
  let out = code;
  for (const p of patches) {
    out = out.slice(0, p.start) + p.text + out.slice(p.end);
  }
  return out;
}

function topLevelFns(ast) {
  const out = [];
  const body = (ast && ast.program && ast.program.body) || ast.body || [];
  for (const stmt of body) {
    if (stmt.type === "FunctionDeclaration" && stmt.id && stmt.id.name) {
      out.push({
        name: stmt.id.name,
        async: !!stmt.async,
        start: stmt.start,
        end: stmt.end,
      });
    } else if (stmt.type === "VariableDeclaration") {
      for (const d of stmt.declarations || []) {
        const id = d.id && d.id.type === "Identifier" ? d.id.name : null;
        const init = d.init;
        if (!id || !init) continue;
        if (
          init.type === "FunctionExpression" ||
          init.type === "ArrowFunctionExpression"
        ) {
          out.push({
            name: id,
            async: !!init.async,
            start: stmt.start,
            end: stmt.end,
          });
        }
      }
    }
  }
  return out;
}

function identifierHits(ast, names) {
  const want = new Set(names);
  const hits = [];
  walk(ast, (node, parent) => {
    if (node.type !== "Identifier" || !want.has(node.name)) return;
    if (parent && parent.type === "MemberExpression" && parent.property === node && !parent.computed) return;
    if (parent && (parent.type === "Property" || parent.type === "ObjectProperty") && parent.key === node && !parent.computed && !parent.shorthand) return;
    if (parent && parent.type === "MethodDefinition" && parent.key === node) return;
    hits.push({ start: node.start, end: node.end, name: node.name });
  });
  return hits;
}

function callNames(ast) {
  const names = [];
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    const cal = node.callee;
    if (cal && cal.type === "Identifier") names.push(cal.name);
  });
  return names;
}

function stringLiteralsFromArray(node) {
  const out = [];
  if (!node || node.type !== "ArrayExpression") return out;
  for (const el of node.elements || []) {
    const v = stringLiteralValue(el);
    if (v) out.push(v);
  }
  return out;
}

function collectArrayBindings(ast) {
  const arrays = {};
  walk(ast, (node) => {
    if (node.type === "VariableDeclarator" && node.id && node.id.type === "Identifier" && node.init) {
      const strs = stringLiteralsFromArray(node.init);
      if (strs.length) arrays[node.id.name] = strs;
    }
    if (
      node.type === "AssignmentExpression" &&
      node.left &&
      node.left.type === "Identifier" &&
      node.right
    ) {
      const strs = stringLiteralsFromArray(node.right);
      if (strs.length) arrays[node.left.name] = strs;
    }
  });
  return arrays;
}

function addMatchingBlocks(node, names, arrays) {
  if (!node) return;
  const lit = stringLiteralValue(node);
  if (lit) {
    names.add(lit);
    return;
  }
  if (node.type === "ArrayExpression") {
    for (const s of stringLiteralsFromArray(node)) names.add(s);
    return;
  }
  if (node.type === "Identifier" && arrays[node.name]) {
    for (const s of arrays[node.name]) names.add(s);
    return;
  }
  if (node.type === "MemberExpression") {
    const prop = memberPropName(node);
    if (prop) names.add(prop);
  }
  walk(node, (inner) => {
    const v = stringLiteralValue(inner);
    if (v) names.add(v);
    if (inner.type === "CallExpression") {
      const nm = nodeName(inner.callee);
      if (nm && /\.includes$/.test(nm) && inner.callee && inner.callee.type === "MemberExpression") {
        const obj = inner.callee.object;
        if (obj && obj.type === "Identifier" && arrays[obj.name]) {
          for (const s of arrays[obj.name]) names.add(s);
        }
        if (obj && obj.type === "ArrayExpression") {
          for (const s of stringLiteralsFromArray(obj)) names.add(s);
        }
      }
    }
    if (inner.type === "MemberExpression") {
      const objName = nodeName(inner.object);
      if (objName && /blocksByName$/.test(objName)) {
        const prop = memberPropName(inner);
        if (prop) names.add(prop);
      }
    }
  });
}

function minedBlockNames(ast) {
  const names = new Set();
  const arrays = collectArrayBindings(ast);
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    const nm = nodeName(node.callee);
    if (nm === "mineBlock") {
      const arg1 = node.arguments && node.arguments[1];
      if (arg1) addMatchingBlocks(arg1, names, arrays);
    }
    if (
      nm === "bot.findBlock" ||
      nm === "bot.findBlocks" ||
      nm === "findBlock" ||
      nm === "findBlocks"
    ) {
      const arg0 = node.arguments && node.arguments[0];
      if (arg0 && arg0.type === "ObjectExpression") {
        for (const p of arg0.properties || []) {
          if (p.type === "SpreadElement" || p.type === "SpreadProperty") continue;
          const key = p.key && (p.key.name || p.key.value);
          if (key === "matching") addMatchingBlocks(p.value, names, arrays);
        }
      } else if (arg0) {
        addMatchingBlocks(arg0, names, arrays);
      }
    }
  });
  return Array.from(names);
}

function numericLiteral(node) {
  if (!node) return null;
  if (node.type === "Literal" && typeof node.value === "number") return node.value;
  if (node.type === "NumericLiteral" && typeof node.value === "number") return node.value;
  if (
    node.type === "UnaryExpression" &&
    node.operator === "-" &&
    node.prefix
  ) {
    const v = numericLiteral(node.argument);
    return v == null ? null : -v;
  }
  return null;
}

function isStringLiteral(node) {
  if (!node) return false;
  if (node.type === "Literal" && typeof node.value === "string") return true;
  if (node.type === "StringLiteral") return true;
  if (node.type === "TemplateLiteral" && (!node.expressions || node.expressions.length === 0)) return true;
  return false;
}

function stringLiteralValue(node) {
  if (!node) return null;
  if (node.type === "Literal" && typeof node.value === "string") return node.value;
  if (node.type === "StringLiteral") return node.value;
  if (node.type === "TemplateLiteral" && (!node.expressions || node.expressions.length === 0)) {
    return (node.quasis && node.quasis[0] && node.quasis[0].value && node.quasis[0].value.cooked) || "";
  }
  return null;
}

function maxDistanceLiteral(obj) {
  if (!obj || (obj.type !== "ObjectExpression")) return { present: false, literal: false, value: null };
  for (const p of obj.properties || []) {
    if (p.type === "SpreadElement" || p.type === "SpreadProperty") continue;
    const key = p.key && (p.key.name || p.key.value);
    if (key !== "maxDistance") continue;
    const v = numericLiteral(p.value);
    if (v == null) return { present: true, literal: false, value: null };
    return { present: true, literal: true, value: v };
  }
  return { present: false, literal: false, value: null };
}

function runGates(code, ast) {
  const reasons = [];
  const slashWords = [
    "/give", "/tp", "/teleport", "/setblock", "/summon", "/fill", "/gamemode",
    "/locate", "/loot", "/item", "/effect", "/time", "/weather",
  ];
  const low = code;
  for (const w of slashWords) {
    if (low.includes(w)) reasons.push("slash_cmd:" + w);
  }
  if (/(^|[^.\w])eval\s*\(/.test(code)) reasons.push("eval");
  if (/(^|[^.\w])Function\s*\(/.test(code)) reasons.push("Function(");
  if (/\bfromCharCode\b/.test(code)) reasons.push("fromCharCode");
  if (/\bbot\.creative\b/.test(code)) reasons.push("bot.creative");
  if (/\bwhile\s*\(\s*true\s*\)/.test(code)) reasons.push("while(true)");
  if (/\bfor\s*\(\s*;\s*;\s*\)/.test(code)) reasons.push("for(;;)");
  if (/(^|[^.\w])obtain\s*\(/.test(code)) reasons.push("v30:obtain");
  if (/(^|[^.\w])gather\s*\(/.test(code)) reasons.push("v30:gather");
  if (/(^|[^.\w])mineOre\s*\(/.test(code)) reasons.push("v30:mineOre");
  if (/(^|[^.\w])craft\s*\(/.test(code)) reasons.push("v30:craft");
  if (/(^|[^.\w])smelt\s*\(/.test(code)) reasons.push("v30:smelt");
  if (/(^|[^.\w])collectFluid\s*\(/.test(code)) reasons.push("v30:collectFluid");

  walk(ast, (node, parent) => {
    if (node.type === "MemberExpression" && node.computed) {
      const obj = node.object;
      if (obj && obj.type === "Identifier" && obj.name === "bot") {
        if (stringLiteralValue(node.property) == null) {
          reasons.push("bot_computed_non_literal");
        }
      }
    }
    if (node.type === "MemberExpression" && isChatMember(node)) {
      const isCall = parent && parent.type === "CallExpression" && parent.callee === node;
      if (!isCall) reasons.push("bot.chat_alias");
    }
    if (node.type === "CallExpression") {
      const cal = node.callee;
      if (cal && isChatMember(cal)) {
        const arg = node.arguments && node.arguments[0];
        const pre = _chatStaticPrefix(arg);
        if (pre === null || pre.trim() === "") {
          reasons.push("bot.chat_non_literal");
        } else if (pre.trimStart().startsWith("/")) {
          reasons.push("bot.chat_slash_literal");
        }
      }
      const nm = nodeName(node.callee);
      if (nm === "bot.findBlock" || nm === "bot.findBlocks" || nm === "findBlock" || nm === "findBlocks") {
        const arg0 = node.arguments && node.arguments[0];
        const md = maxDistanceLiteral(arg0);
        if (!md.present || !md.literal) reasons.push("findBlock_maxDistance_missing");
        else if (md.value > 32) reasons.push("findBlock_maxDistance_gt32");
      }
      if (nm === "exploreUntil") {
        const t = node.arguments && node.arguments[1];
        // 3rd arg is maxTime (index 2). Keep for cap, not a gate.
      }
      const ctor = node.callee && node.callee.type === "MemberExpression"
        ? null
        : (node.callee && node.callee.type === "Identifier" ? node.callee.name : null);
      const callee = node.callee;
      let ctorName = null;
      if (callee && callee.type === "Identifier") ctorName = callee.name;
      if (node.callee && node.callee.type === "MemberExpression") ctorName = null;
      if (["Vec3", "GoalBlock", "GoalNear", "GoalXZ"].indexOf(ctorName) >= 0 ||
          (node.type === "NewExpression" && node.callee && node.callee.type === "Identifier" &&
            ["Vec3", "GoalBlock", "GoalNear", "GoalXZ"].indexOf(node.callee.name) >= 0)) {
        // handled below via NewExpression
      }
    }
    if (node.type === "NewExpression" && node.callee && node.callee.type === "Identifier") {
      const cn = node.callee.name;
      if (cn === "Vec3" || cn === "GoalBlock" || cn === "GoalNear" || cn === "GoalXZ") {
        const nums = (node.arguments || []).map(numericLiteral).filter((v) => v != null);
        if (nums.some((v) => Math.abs(v) > 1)) reasons.push("abs_coord:" + cn);
      }
    }
    if (node.type === "ObjectExpression") {
      const md = maxDistanceLiteral(node);
      if (md.present && md.literal && md.value > 32) reasons.push("maxDistance_gt32");
    }
  });
  return Array.from(new Set(reasons));
}

function capExplore(code, ast) {
  const patches = [];
  const normalized = [];
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    const nm = nodeName(node.callee);
    if (nm !== "exploreUntil") return;
    const arg = node.arguments && node.arguments[2];
    const v = numericLiteral(arg);
    if (v != null && v > 60) {
      patches.push({ start: arg.start, end: arg.end, text: "60" });
      normalized.push("exploreUntil_maxTime_capped:" + v);
    }
  });
  return { code: applyPatches(code, patches), normalized };
}

function subtreeHasAwait(root) {
  let found = false;
  walk(root, (node) => {
    if (node.type === "AwaitExpression") found = true;
  });
  return found;
}

function insertAborts(code, ast, candId) {
  const probe = abortProbe(candId);
  const patches = [];
  walk(ast, (node) => {
    if (
      node.type === "WhileStatement" ||
      node.type === "ForStatement" ||
      node.type === "DoWhileStatement" ||
      node.type === "ForInStatement" ||
      node.type === "ForOfStatement"
    ) {
      const body = node.body;
      if (body) {
        if (body.type === "BlockStatement") {
          const insertAt = (body.start || 0) + 1;
          patches.push({ start: insertAt, end: insertAt, text: probe });
        } else {
          patches.push({
            start: body.start,
            end: body.end,
            text: "{" + probe + code.slice(body.start, body.end) + "}",
          });
        }
      }
    }
    if (node.type === "BlockStatement" && Array.isArray(node.body)) {
      for (const stmt of node.body) {
        if (!stmt || stmt.start == null || stmt.end == null) continue;
        if (subtreeHasAwait(stmt)) {
          patches.push({ start: stmt.end, end: stmt.end, text: probe });
        }
      }
    }
  });
  const out = applyPatches(code, patches);
  return { code: out, normalized: patches.length ? ["abort_probes:" + patches.length] : [] };
}

function openParenAfter(code, end) {
  let i = end;
  while (i < code.length && /\s/.test(code[i])) i++;
  if (code[i] === "(") return i;
  return -1;
}

function rewriteChatAndToss(code, ast) {
  const patches = [];
  const notes = [];
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    const cal = node.callee;
    if (!cal || cal.type !== "MemberExpression") return;
    const paren = openParenAfter(code, cal.end);
    if (paren < 0) return;
    if (isChatMember(cal) && !cal.computed && nodeName(cal) === "bot.chat") {
      patches.push({ start: cal.start, end: paren + 1, text: "_govSafeChat(bot, " });
      notes.push("rewrite_chat");
    } else if (isTossMember(cal) && !cal.computed && nodeName(cal) === "bot.toss") {
      patches.push({ start: cal.start, end: paren + 1, text: "_govNoToss(" });
      notes.push("rewrite_toss");
    }
  });
  return { code: applyPatches(code, patches), normalized: notes };
}

function craftOwnKeyReasons(ast, itemKey) {
  const reasons = [];
  const key = String(itemKey || "").toLowerCase();
  if (!key) return reasons;
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    const nm = nodeName(node.callee);
    if (nm !== "craftItem" && nm !== "smeltItem") return;
    const arg = node.arguments && node.arguments[1];
    const v = stringLiteralValue(arg);
    if (v && String(v).toLowerCase().indexOf(key) >= 0) {
      reasons.push("craft_own_key:" + v);
    }
  });
  return reasons;
}

function renameAll(code, ast, prefix, extraNames) {
  const fns = topLevelFns(ast);
  const names = fns.map((f) => f.name);
  if (extraNames) {
    for (const n of extraNames) if (names.indexOf(n) < 0) names.push(n);
  }
  const hits = identifierHits(ast, names);
  const patches = hits.map((h) => ({
    start: h.start,
    end: h.end,
    text: prefix + h.name,
  }));
  const out = applyPatches(code, patches);
  const renamedFns = fns.map((f) => ({
    name: prefix + f.name,
    async: f.async,
    orig: f.name,
  }));
  const asyncFns = renamedFns.filter((f) => f.async);
  const main = asyncFns.length ? asyncFns[asyncFns.length - 1].name : (renamedFns.length ? renamedFns[renamedFns.length - 1].name : null);
  return { code: out, fns: renamedFns, fn: main };
}

function main() {
  const raw = fs.readFileSync(0, "utf8");
  const req = JSON.parse(raw || "{}");
  const parser = tryLoadParser();
  const result = { parser: parser ? parser.kind : null };
  if (!parser) {
    result.error = "no_parser";
    process.stdout.write(JSON.stringify(result));
    return;
  }
  const op = req.op || "gates";
  try {
    if (op === "gates") {
      const ast = parser.parse(req.code);
      result.reasons = runGates(req.code, ast);
      result.ok = result.reasons.length === 0;
      result.fns = topLevelFns(ast);
      result.calls = callNames(ast);
    } else if (op === "normalize") {
      let code = req.code;
      let ast = parser.parse(code);
      const notes = [];
      const c1 = capExplore(code, ast);
      code = c1.code;
      notes.push.apply(notes, c1.normalized);
      ast = parser.parse(code);
      result.code = code;
      result.normalized = notes;
      result.fns = topLevelFns(ast);
      result.calls = callNames(ast);
      const asyncFns = result.fns.filter((f) => f.async);
      result.fn_orig = asyncFns.length ? asyncFns[asyncFns.length - 1].name : (result.fns.length ? result.fns[result.fns.length - 1].name : null);
    } else if (op === "instrument") {
      let code = req.code;
      let ast = parser.parse(code);
      const notes = [];
      const c2 = insertAborts(code, ast, req.cand_id || "");
      code = c2.code;
      notes.push.apply(notes, c2.normalized);
      ast = parser.parse(code);
      const own = craftOwnKeyReasons(ast, req.item_key || "");
      result.craft_own_key = own;
      const c3 = rewriteChatAndToss(code, ast);
      code = c3.code;
      notes.push.apply(notes, c3.normalized);
      ast = parser.parse(code);
      result.code = code;
      result.normalized = notes;
      result.fns = topLevelFns(ast);
      result.calls = callNames(ast);
    } else if (op === "rename") {
      const ast = parser.parse(req.code);
      const r = renameAll(req.code, ast, req.prefix || "", req.names || []);
      result.code = r.code;
      result.fns = r.fns;
      result.fn = r.fn;
    } else if (op === "calls") {
      const ast = parser.parse(req.code);
      result.calls = callNames(ast);
      result.fns = topLevelFns(ast);
    } else if (op === "mined_blocks") {
      const ast = parser.parse(req.code);
      result.blocks = minedBlockNames(ast);
      result.calls = callNames(ast);
      result.fns = topLevelFns(ast);
    } else {
      result.error = "unknown_op";
    }
  } catch (e) {
    result.error = String(e && e.message ? e.message : e);
  }
  process.stdout.write(JSON.stringify(result));
}

main();
