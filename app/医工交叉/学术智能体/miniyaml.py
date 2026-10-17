# -*- coding: utf-8 -*-
"""极简 YAML 子集解析器（环境里没有 PyYAML 时的兜底）。

支持的写法（覆盖 task.example.yaml 用到的全部语法）：
  - 注释：以 # 开头，或空白后的 #（引号/方括号内不算）
  - 缩进映射：key: value，子层用空格缩进
  - 缩进列表：key 下方的 "- 条目"，也支持与 key 同缩进的 "- 条目"
  - 行内列表：key: [a, b, c]
  - 标量：带引号字符串 / true/false / 整数 / 浮点 / 普通文本

遇到不支持的写法会抛出带行号的 YamlError（调用方转成友好中文提示）。
"""
import re


class YamlError(ValueError):
    """带行号的解析错误。"""

    def __init__(self, message, line=None):
        super().__init__(message)
        self.line = line


def _strip_comment(line):
    """去掉行内注释，保留引号/括号内的 #。"""
    out = []
    quote = None
    depth = 0
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch in "[{":
            depth += 1
            out.append(ch)
        elif ch in "]}":
            depth = max(0, depth - 1)
            out.append(ch)
        elif ch == "#" and depth == 0 and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _find_colon(text):
    """找顶层冒号（后面要有空格或行尾），返回下标，找不到返回 -1。"""
    quote = None
    depth = 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0 and (i + 1 >= len(text) or text[i + 1] in " \t"):
            return i
    return -1


def _split_commas(text):
    """按顶层逗号切分（忽略引号内的逗号）。"""
    parts = []
    buf = []
    quote = None
    depth = 0
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _scalar(raw, lineno):
    s = (raw or "").strip()
    if not s:
        return ""
    if s[0] == "[" and s[-1] == "]":
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part, lineno) for part in _split_commas(inner)]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", "none"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    if re.fullmatch(r"[-+]?\d*\.\d+", s):
        try:
            return float(s)
        except ValueError:
            pass
    return s


def _parse_block(lines, start, indent):
    text = lines[start][1]
    if text == "-" or text.startswith("- "):
        return _parse_list(lines, start, indent)
    return _parse_map(lines, start, indent)


def _parse_map(lines, start, indent):
    result = {}
    i = start
    n = len(lines)
    while i < n:
        ind, text, no = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError("第 %d 行：缩进比上一层多，请检查空格缩进" % no, no)
        if text == "-" or text.startswith("- "):
            break
        c = _find_colon(text)
        if c < 0:
            raise YamlError("第 %d 行：不是「键: 值」写法" % no, no)
        key = text[:c].strip().strip("\"'")
        rest = text[c + 1:].strip()
        if key == "":
            raise YamlError("第 %d 行：冒号前缺少字段名" % no, no)
        if rest != "":
            result[key] = _scalar(rest, no)
            i += 1
            continue
        # 空值 → 看下一层
        if i + 1 < n and lines[i + 1][0] > indent:
            value, i = _parse_block(lines, i + 1, lines[i + 1][0])
        elif i + 1 < n and lines[i + 1][0] == indent and (
                lines[i + 1][1] == "-" or lines[i + 1][1].startswith("- ")):
            value, i = _parse_list(lines, i + 1, indent)
        else:
            value = None
            i += 1
        result[key] = value
    return result, i


def _parse_list(lines, start, indent):
    items = []
    i = start
    n = len(lines)
    while i < n:
        ind, text, no = lines[i]
        if ind > indent:
            raise YamlError("第 %d 行：列表项缩进不一致" % no, no)
        if ind < indent or not (text == "-" or text.startswith("- ")):
            break
        body = "" if text == "-" else text[2:].strip()
        c = _find_colon(body) if body else -1
        if c >= 0 and not body.startswith("["):
            # 形如 "- key: value" 的字典项：把后续更深层的行一起交给映射解析
            j = i + 1
            sub = [(indent + 2, body, no)]
            while j < n and lines[j][0] > indent:
                sub.append(lines[j])
                j += 1
            base = min(x[0] for x in sub)
            sub = [(x[0] - base + indent + 2, x[1], x[2]) for x in sub]
            value, _ = _parse_block(sub, 0, indent + 2)
            items.append(value)
            i = j
        else:
            items.append(_scalar(body, no))
            i += 1
    return items, i


def parse(text):
    """解析 YAML 文本，返回 Python 对象；失败抛 YamlError。"""
    lines = []
    for no, raw in enumerate((text or "").splitlines(), 1):
        lead = raw[:len(raw) - len(raw.lstrip(" \t"))]
        if "\t" in lead:
            raise YamlError("第 %d 行：缩进请用空格，不要用 Tab" % no, no)
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip(), no))
    if not lines:
        raise YamlError("内容为空", None)
    if lines[0][0] != 0:
        raise YamlError("第 %d 行：顶层不应缩进" % lines[0][2], lines[0][2])
    value, idx = _parse_block(lines, 0, 0)
    if idx < len(lines):
        raise YamlError("第 %d 行：缩进格式有误，请对照 task.example.yaml" % lines[idx][2],
                        lines[idx][2])
    return value
