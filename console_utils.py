# -*- coding: utf-8 -*-
"""
控制台安全打印。

Windows 中文系统默认 GBK 编码，✅ 等符号打不出来会抛异常；
Streamlit 运行时输出流状态又比较特殊（可能已关闭）。
所以这里做降级：GBK 打不出就替换成普通字符；连降级都失败就放弃——绝不崩溃。
"""


def safe_print(msg=""):
    """任何环境下都能安全打印（打不出的字符自动替换）。"""
    try:
        print(msg)
    except Exception:
        try:
            print(str(msg).encode("gbk", "replace").decode("gbk", "replace"))
        except Exception:
            pass
