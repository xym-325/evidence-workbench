"""控制台输出编码。

规则: 重定向/管道时强制 UTF-8(方便存档与二次处理), 交互终端保持系统码页,
这样在中文 Windows 控制台下中文不会乱码。
"""

from __future__ import annotations

import sys


def enable_utf8_redirects() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream.isatty():
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
