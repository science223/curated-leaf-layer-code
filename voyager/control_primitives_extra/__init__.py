"""从技能库里提炼出来、提升到基础层的组合技能（09-22）。

为什么单独一个目录：`load_control_primitives()` 会把 `control_primitives/` 整个目录
都加载进执行环境，文件丢进去就会**影响所有臂**。放这里，默认不加载，
只有显式传名字（`extra_base_skills`）才生效——关着时与改动前逐字节相同。
"""
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


def load_extra_base_skills(primitive_names=None):
    """primitive_names 为 None 或空 → 返回空列表（默认行为，不改变任何臂）。"""
    if not primitive_names:
        return []
    return [
        U.load_text(path)
        for path in _resolve("control_primitives_extra", primitive_names)
    ]
