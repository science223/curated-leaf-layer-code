// 从 stdin 读一段 JavaScript，用 babel 解析出顶层函数声明，以 JSON 写回 stdout。
//
// 为什么要有这个文件：原来这一步走 JSPyBridge（常驻 node 桥 + 跨进程属性访问），
// 每次属性访问有 10 秒硬超时（javascript/proxy.py:39）。2026-09-02 起该桥频繁
// 无响应，86% 的模型回复卡在 "Timed out accessing 'require'"，代码根本没被执行。
// 换成一次性子进程后没有常驻状态，也没有属性级往返。
//
// 输出契约（与原来 process_ai_message 里用到的字段一一对应）：
//   { n_body: <program.body 的节点数>,
//     functions: [ { name, type, body, params: [{name, type, default, kind}] } ] }
//   PARSE_MODE=invocation 时：
//     { name, args: [{kind, name?, value?}] } 或 { error: "..." }
//   出错时： { error: "..." }

const babel = require("@babel/core");
const generate = require("@babel/generator").default;

function paramInfo(p) {
  if (p.type === "Identifier") {
    return {
      name: p.name,
      type: p.type,
      default: null,
      kind: "identifier",
    };
  }
  if (p.type === "AssignmentPattern") {
    const left = p.left;
    const name = left && left.type === "Identifier" ? left.name : null;
    let defSrc = null;
    try {
      defSrc = generate(p.right).code;
    } catch (e) {
      defSrc = null;
    }
    return {
      name,
      type: p.type,
      default: defSrc,
      kind: "assignment",
    };
  }
  if (p.type === "RestElement") {
    const arg = p.argument;
    const name = arg && arg.type === "Identifier" ? arg.name : null;
    return {
      name,
      type: p.type,
      default: null,
      kind: "rest",
    };
  }
  return {
    name: typeof p.name === "string" ? p.name : null,
    type: p.type,
    default: null,
    kind: "other",
  };
}

function literalArg(node) {
  if (!node) return null;
  if (
    node.type === "NumericLiteral" ||
    node.type === "NumberLiteral" ||
    node.type === "StringLiteral" ||
    node.type === "BooleanLiteral"
  ) {
    return { kind: "literal", value: node.value };
  }
  if (node.type === "NullLiteral") {
    return { kind: "literal", value: null };
  }
  if (
    node.type === "UnaryExpression" &&
    node.operator === "-" &&
    node.prefix &&
    (node.argument.type === "NumericLiteral" ||
      node.argument.type === "NumberLiteral")
  ) {
    return { kind: "literal", value: -node.argument.value };
  }
  if (node.type === "Identifier") {
    return { kind: "identifier", name: node.name };
  }
  return { kind: "other" };
}

function parseProgram(src) {
  const ast = babel.parse(src);
  const functions = [];
  for (const node of ast.program.body) {
    if (node.type !== "FunctionDeclaration") continue;
    functions.push({
      name: node.id ? node.id.name : null,
      type: node.async ? "AsyncFunctionDeclaration" : "FunctionDeclaration",
      body: generate(node).code,
      // 原实现用 params[0].name 做断言，这里保留 name 字段；
      // default / kind 是 WP-E 新增，关闭开关时现有断言不读它们。
      params: node.params.map(paramInfo),
    });
  }
  return { n_body: ast.program.body.length, functions };
}

function parseInvocation(src) {
  const ast = babel.parse(src);
  if (ast.program.body.length !== 1) {
    return { error: "Invocation must be a single await NAME(...) statement" };
  }
  const stmt = ast.program.body[0];
  if (stmt.type !== "ExpressionStatement") {
    return { error: "Invocation must be a single await NAME(...) statement" };
  }
  const expr = stmt.expression;
  if (expr.type !== "AwaitExpression" || !expr.argument) {
    return { error: "Invocation must be a single await NAME(...) statement" };
  }
  const call = expr.argument;
  if (call.type !== "CallExpression") {
    return { error: "Invocation must be a single await NAME(...) statement" };
  }
  const callee = call.callee;
  if (!callee || callee.type !== "Identifier") {
    return { error: "Invocation must be a single await NAME(...) statement" };
  }
  const args = (call.arguments || []).map(literalArg);
  return { name: callee.name, args };
}

function walkSkipMeta(node, parent, visit) {
  if (!node || typeof node !== "object") return;
  visit(node, parent);
  for (const k of Object.keys(node)) {
    if (
      k === "leadingComments" ||
      k === "trailingComments" ||
      k === "innerComments" ||
      k === "loc" ||
      k === "start" ||
      k === "end" ||
      k === "range"
    ) {
      continue;
    }
    const v = node[k];
    if (Array.isArray(v)) v.forEach((x) => walkSkipMeta(x, node, visit));
    else if (v && typeof v === "object" && v.type) walkSkipMeta(v, node, visit);
  }
}

function collectRefs(body, names, selfName) {
  const refs = new Set();
  walkSkipMeta(body, null, (node, parent) => {
    if (!node || node.type !== "Identifier") return;
    if (!names.has(node.name) || node.name === selfName) return;
    const isProp =
      parent &&
      parent.type === "MemberExpression" &&
      parent.property === node &&
      !parent.computed;
    const isKey =
      parent &&
      (parent.type === "ObjectProperty" || parent.type === "ObjectMethod") &&
      parent.key === node &&
      !parent.computed;
    if (!isProp && !isKey) refs.add(node.name);
  });
  return [...refs];
}

function parseCallGraph(src) {
  const ast = babel.parse(src);
  const entries = [];
  for (const node of ast.program.body) {
    if (node.type === "FunctionDeclaration" && node.id && node.id.name) {
      entries.push({
        name: node.id.name,
        async: !!node.async,
        type: node.async ? "AsyncFunctionDeclaration" : "FunctionDeclaration",
        params: node.params.map(paramInfo),
        bodyNode: node.body,
        body: generate(node).code,
      });
    }
    if (node.type !== "VariableDeclaration") continue;
    for (const d of node.declarations) {
      if (!d.id || d.id.type !== "Identifier" || !d.init) continue;
      const init = d.init;
      if (
        init.type !== "ArrowFunctionExpression" &&
        init.type !== "FunctionExpression"
      ) {
        continue;
      }
      entries.push({
        name: d.id.name,
        async: !!init.async,
        type: init.type,
        params: init.params.map(paramInfo),
        bodyNode: init.body,
        body: generate(node).code,
      });
    }
  }
  const names = new Set(entries.map((e) => e.name));
  const functions = entries.map((e) => ({
    name: e.name,
    async: e.async,
    type: e.type,
    params: e.params,
    body: e.body,
    refs: collectRefs(e.bodyNode, names, e.name),
  }));
  return { n_body: ast.program.body.length, functions };
}

let src = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => {
  src += d;
});
process.stdin.on("end", () => {
  let out;
  try {
    const mode = process.env.PARSE_MODE || "program";
    if (mode === "invocation") out = parseInvocation(src);
    else if (mode === "callgraph") out = parseCallGraph(src);
    else out = parseProgram(src);
  } catch (e) {
    out = { error: (e && e.message) || String(e) };
  }
  process.stdout.write(JSON.stringify(out));
});
