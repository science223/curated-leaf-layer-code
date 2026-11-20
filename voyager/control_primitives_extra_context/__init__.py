"""上面那批提炼技能给模型看的简化版（带调用示例）。默认不加载。"""
import os

import pkg_resources

import voyager.utils as U


def _resolve(subdir, primitive_names):
    """把名字解析成文件路径；名字打错当场报错，不许静默少注入。"""
    package_path = pkg_resources.resource_filename("voyager", "")
    root = os.path.join(package_path, subdir)
    available = sorted(f[:-3] for f in os.listdir(root) if f.endswith(".js"))
    paths = []
    for name in primitive_names:
        if name not in available:
            raise ValueError(f"{subdir} 里没有 {name!r}；可用的是 {available}")
        paths.append(os.path.join(root, name + ".js"))
    return paths


USE_RULE_PREFIX = "//! USE-RULE:"


def _split_rule(text):
    """→ (给模型看的代码, 名单里那一行)。没有规则行时第二项是 ""。"""
    if not text.startswith(USE_RULE_PREFIX):
        return text, ""
    head, _, rest = text.partition("\n")
    return rest, head[len(USE_RULE_PREFIX):].strip()


def load_extra_base_skills_context(primitive_names=None):
    """展示给模型的代码（规则行已剥掉，格式与 11 个内置原语一致）。"""
    if not primitive_names:
        return []
    from voyager.named_action_docs import is_v30_named, render_named_action_context
    from voyager.control_primitives_extra import load_extra_base_skills

    out = []
    for name, path in zip(
        primitive_names, _resolve("control_primitives_extra_context", primitive_names)
    ):
        if is_v30_named(name):
            impl = load_extra_base_skills([name])[0]
            text = render_named_action_context(name, impl)
        else:
            text = U.load_text(path)
        out.append(_split_rule(text)[0])
    return out


def load_extra_base_skill_rules(primitive_names=None):
    """提示词第 2 条那张名单要多出来的几行。

    09-22 实测：不进这张名单的东西，模型调用数恰好是 0。
    """
    if not primitive_names:
        return ""
    from voyager.named_action_docs import is_v30_named, render_named_action_context
    from voyager.control_primitives_extra import load_extra_base_skills

    out = []
    for name, path in zip(
        primitive_names, _resolve("control_primitives_extra_context", primitive_names)
    ):
        if is_v30_named(name):
            impl = load_extra_base_skills([name])[0]
            text = render_named_action_context(name, impl)
        else:
            text = U.load_text(path)
        rule = _split_rule(text)[1]
        if rule:
            out.append("        " + rule + "\n")
    return "".join(out)
