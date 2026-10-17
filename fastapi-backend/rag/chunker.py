"""
Code-aware chunking: Tree-sitter → Regex fallback → Line fallback.

Layered chunking strategy:
1. Tree-sitter: Accurate syntax-tree parsing for Python, JS, TS, and others.
2. Regex: Pattern-based splitting when tree-sitter fails or isn't available.
3. Line: Simple line-based chunks with overlap as the final fallback.

Every chunker returns List[CodeChunk] with rich metadata including
symbol name, type, parent, line ranges, and language.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

logger = logging.getLogger("chunker")

# Try to import tree-sitter-languages
try:
    from tree_sitter_languages import get_parser, get_language
    TREE_SITTER_AVAILABLE = True
    logger.info("Tree-sitter-languages loaded successfully")
except ImportError:
    TREE_SITTER_AVAILABLE = False
    logger.warning("tree-sitter-languages not installed, falling back to regex/line chunking")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CodeChunk:
    """A single chunk of source code with rich metadata."""
    content: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    symbol_type: str = "module"         # module, class, function, method, imports, unknown
    symbol_name: str = ""
    parent_symbol: Optional[str] = None
    parent_type: Optional[str] = None
    chunk_index: int = 0
    part_index: int = 0                 # for oversized symbols split into parts
    part_count: int = 1
    imports: list[str] = field(default_factory=list)
    parser_used: str = "unknown"        # tree_sitter, regex, line
    content_hash: str = ""

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = hashlib.md5(self.content.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".cpp": "cpp",
    ".c": "c",
    ".swift": "swift",
    ".kt": "kotlin",
    ".rs": "rust",
    ".vue": "vue",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
}

# Languages tree-sitter-languages supports well
TREE_SITTER_SUPPORTED = {
    "python", "javascript", "typescript", "java", "go",
    "ruby", "php", "c_sharp", "cpp", "c", "rust", "kotlin",
}


def detect_language(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    return EXTENSION_TO_LANGUAGE.get(ext, "unknown")


# ---------------------------------------------------------------------------
# Tree-sitter Chunker (Accuracy: HIGH)
# ---------------------------------------------------------------------------

# Node types we care about per language
_SYMBOL_NODES = {
    "python": {
        "top_level": {"function_definition", "class_definition", "decorated_definition"},
        "class_children": {"function_definition", "decorated_definition"},
        "import": {"import_statement", "import_from_statement"},
    },
    "javascript": {
        "top_level": {
            "function_declaration", "class_declaration", "export_statement",
            "lexical_declaration", "variable_declaration",
        },
        "class_children": {"method_definition", "field_definition"},
        "import": {"import_statement"},
    },
    "typescript": {
        "top_level": {
            "function_declaration", "class_declaration", "export_statement",
            "lexical_declaration", "variable_declaration", "interface_declaration",
            "type_alias_declaration", "enum_declaration",
        },
        "class_children": {"method_definition", "public_field_definition", "field_definition"},
        "import": {"import_statement"},
    },
    "java": {
        "top_level": {"class_declaration", "interface_declaration", "enum_declaration"},
        "class_children": {"method_declaration", "constructor_declaration", "field_declaration"},
        "import": {"import_declaration"},
    },
    "go": {
        "top_level": {"function_declaration", "method_declaration", "type_declaration"},
        "class_children": set(),
        "import": {"import_declaration"},
    },
}


def _extract_symbol_name(node, source_bytes: bytes, language: str) -> str:
    """Extract the symbol name from a tree-sitter node."""
    # For decorated definitions, drill into the actual definition
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return _extract_symbol_name(child, source_bytes, language)

    # For export statements, drill into the declaration
    if node.type == "export_statement":
        for child in node.children:
            if child.type in (
                "function_declaration", "class_declaration",
                "lexical_declaration", "variable_declaration",
            ):
                return _extract_symbol_name(child, source_bytes, language)

    # For lexical_declaration (const x = ...), get the variable name
    if node.type in ("lexical_declaration", "variable_declaration"):
        for child in node.children:
            if child.type in ("variable_declarator", "lexical_binding"):
                for grandchild in child.children:
                    if grandchild.type in ("identifier", "property_identifier"):
                        return source_bytes[grandchild.start_byte:grandchild.end_byte].decode("utf-8", errors="replace")

    # Standard: look for 'name' or 'identifier' child
    for child in node.children:
        if child.type == "identifier" or child.type == "name":
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        if child.type == "property_identifier":
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

    return "<anonymous>"


def _node_to_symbol_type(node_type: str, is_child_of_class: bool) -> str:
    """Map tree-sitter node type to our symbol type."""
    if "class" in node_type or "interface" in node_type or "enum" in node_type:
        return "class"
    if is_child_of_class:
        return "method"
    if "function" in node_type or "method" in node_type or "constructor" in node_type:
        return "function"
    if "import" in node_type:
        return "imports"
    if "declaration" in node_type:
        return "function"  # const x = () => {} etc.
    if node_type == "decorated_definition":
        return "function"
    if node_type == "export_statement":
        return "function"
    return "unknown"


def _extract_imports(root_node, source_bytes: bytes, language: str) -> list[str]:
    """Extract import module names from the AST."""
    lang_config = _SYMBOL_NODES.get(language, {})
    import_types = lang_config.get("import", set())
    imports = []

    for node in root_node.children:
        if node.type in import_types:
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            # Extract module names from import text
            if language == "python":
                match = re.search(r"(?:from|import)\s+([\w.]+)", text)
                if match:
                    imports.append(match.group(1).split(".")[0])
            elif language in ("javascript", "typescript"):
                match = re.search(r"from\s+['\"]([^'\"]+)['\"]", text)
                if match:
                    imports.append(match.group(1))
            elif language == "java":
                match = re.search(r"import\s+([\w.]+)", text)
                if match:
                    imports.append(match.group(1))

    return list(set(imports))


def _build_parent_context(
    class_name: str,
    class_node,
    source_bytes: bytes,
) -> str:
    """Build a minimal class context header for method chunks.

    Includes the class signature + docstring, but NOT method bodies.
    """
    # Get class first line (signature)
    start = class_node.start_byte
    first_line_end = source_bytes.find(b"\n", start)
    if first_line_end == -1:
        first_line_end = class_node.end_byte
    class_sig = source_bytes[start:first_line_end + 1].decode("utf-8", errors="replace")

    # Look for docstring in class body
    docstring = ""
    for child in class_node.children:
        if child.type == "block" or child.type == "class_body":
            for block_child in child.children:
                if block_child.type == "expression_statement":
                    for expr_child in block_child.children:
                        if expr_child.type == "string":
                            doc_text = source_bytes[expr_child.start_byte:expr_child.end_byte].decode("utf-8", errors="replace")
                            docstring = f"    {doc_text}\n"
                    break
            break

    return f"{class_sig}{docstring}"


def _split_oversized(
    content: str,
    metadata: dict,
    max_chunk_size: int,
) -> list[CodeChunk]:
    """Split an oversized symbol into multiple parts, preserving metadata."""
    lines = content.split("\n")
    parts: list[CodeChunk] = []
    current_lines: list[str] = []
    current_size = 0
    start_line = metadata["start_line"]

    for i, line in enumerate(lines):
        line_size = len(line) + 1
        if current_size + line_size > max_chunk_size and current_lines:
            parts.append(CodeChunk(
                content="\n".join(current_lines),
                file_path=metadata["file_path"],
                language=metadata["language"],
                start_line=start_line,
                end_line=start_line + len(current_lines) - 1,
                symbol_type=metadata["symbol_type"],
                symbol_name=metadata["symbol_name"],
                parent_symbol=metadata.get("parent_symbol"),
                parent_type=metadata.get("parent_type"),
                imports=metadata.get("imports", []),
                parser_used=metadata["parser_used"],
            ))
            start_line = start_line + len(current_lines)
            current_lines = []
            current_size = 0

        current_lines.append(line)
        current_size += line_size

    if current_lines:
        parts.append(CodeChunk(
            content="\n".join(current_lines),
            file_path=metadata["file_path"],
            language=metadata["language"],
            start_line=start_line,
            end_line=start_line + len(current_lines) - 1,
            symbol_type=metadata["symbol_type"],
            symbol_name=metadata["symbol_name"],
            parent_symbol=metadata.get("parent_symbol"),
            parent_type=metadata.get("parent_type"),
            imports=metadata.get("imports", []),
            parser_used=metadata["parser_used"],
        ))

    # Set part indices
    for i, part in enumerate(parts):
        part.part_index = i
        part.part_count = len(parts)

    return parts


class TreeSitterChunker:
    """Chunk source code using tree-sitter AST parsing.

    Produces symbol-aware chunks with parent→child relationships.
    """

    MAX_CHUNK_SIZE = 3000  # characters

    def chunk(
        self,
        content: str,
        language: str,
        file_path: str,
    ) -> list[CodeChunk]:
        if not TREE_SITTER_AVAILABLE:
            return []

        if language not in TREE_SITTER_SUPPORTED:
            return []

        lang_config = _SYMBOL_NODES.get(language)
        if not lang_config:
            return []

        try:
            parser = get_parser(language)
        except Exception as e:
            logger.warning("Tree-sitter parser unavailable for %s: %s", language, e)
            return []

        source_bytes = content.encode("utf-8")
        try:
            tree = parser.parse(source_bytes)
        except Exception as e:
            logger.warning("Tree-sitter parse failed for %s: %s", file_path, e)
            return []

        root = tree.root_node
        if root.has_error and root.child_count == 0:
            # Completely unparseable — fall through to regex
            return []

        chunks: list[CodeChunk] = []
        file_imports = _extract_imports(root, source_bytes, language)
        top_level_types = lang_config["top_level"]
        class_child_types = lang_config["class_children"]
        import_types = lang_config.get("import", set())

        # Collect import nodes into a single chunk
        import_lines: list[str] = []
        import_start = None
        import_end = None

        # Track which byte ranges are covered by symbol chunks
        covered_ranges: list[tuple[int, int]] = []

        for node in root.children:
            # Imports
            if node.type in import_types:
                text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                import_lines.append(text)
                if import_start is None:
                    import_start = node.start_point[0] + 1
                import_end = node.end_point[0] + 1
                covered_ranges.append((node.start_byte, node.end_byte))
                continue

            # Symbol nodes
            if node.type not in top_level_types:
                continue

            covered_ranges.append((node.start_byte, node.end_byte))

            # Determine if this is a class
            actual_node = node
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type in ("class_definition", "function_definition"):
                        actual_node = child
                        break
            if node.type == "export_statement":
                for child in node.children:
                    if child.type in ("class_declaration", "function_declaration",
                                       "lexical_declaration", "variable_declaration"):
                        actual_node = child
                        break

            is_class = "class" in actual_node.type

            if is_class:
                # Process class: create class signature chunk + method chunks
                class_name = _extract_symbol_name(node, source_bytes, language)
                parent_ctx = _build_parent_context(class_name, actual_node, source_bytes)

                # Class signature chunk (first few lines)
                class_sig_text = parent_ctx.strip()
                chunks.append(CodeChunk(
                    content=class_sig_text,
                    file_path=file_path,
                    language=language,
                    start_line=node.start_point[0] + 1,
                    end_line=node.start_point[0] + class_sig_text.count("\n") + 1,
                    symbol_type="class",
                    symbol_name=class_name,
                    parent_symbol=None,
                    imports=file_imports,
                    parser_used="tree_sitter",
                ))

                # Find the class body and extract methods
                class_body = None
                for child in actual_node.children:
                    if child.type in ("block", "class_body", "declaration_list"):
                        class_body = child
                        break

                if class_body:
                    for member in class_body.children:
                        member_actual = member
                        if member.type == "decorated_definition":
                            for mc in member.children:
                                if mc.type in class_child_types:
                                    member_actual = mc
                                    break

                        if member.type in class_child_types or member_actual.type in class_child_types:
                            method_name = _extract_symbol_name(member, source_bytes, language)
                            method_text = source_bytes[member.start_byte:member.end_byte].decode("utf-8", errors="replace")

                            # Prepend parent context
                            full_content = f"# File: {file_path}\n{parent_ctx}\n{method_text}"

                            meta = {
                                "file_path": file_path,
                                "language": language,
                                "start_line": member.start_point[0] + 1,
                                "end_line": member.end_point[0] + 1,
                                "symbol_type": "method",
                                "symbol_name": method_name,
                                "parent_symbol": class_name,
                                "parent_type": "class",
                                "imports": file_imports,
                                "parser_used": "tree_sitter",
                            }

                            if len(full_content) > self.MAX_CHUNK_SIZE:
                                chunks.extend(_split_oversized(full_content, meta, self.MAX_CHUNK_SIZE))
                            else:
                                chunks.append(CodeChunk(
                                    content=full_content,
                                    **{k: v for k, v in meta.items()},
                                ))
            else:
                # Standalone function / variable / export
                symbol_name = _extract_symbol_name(node, source_bytes, language)
                symbol_type = _node_to_symbol_type(node.type, is_child_of_class=False)
                text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                full_content = f"# File: {file_path}\n{text}"

                meta = {
                    "file_path": file_path,
                    "language": language,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "symbol_type": symbol_type,
                    "symbol_name": symbol_name,
                    "parent_symbol": None,
                    "parent_type": None,
                    "imports": file_imports,
                    "parser_used": "tree_sitter",
                }

                if len(full_content) > self.MAX_CHUNK_SIZE:
                    chunks.extend(_split_oversized(full_content, meta, self.MAX_CHUNK_SIZE))
                else:
                    chunks.append(CodeChunk(content=full_content, **meta))

        # Add imports chunk
        if import_lines:
            chunks.insert(0, CodeChunk(
                content="\n".join(import_lines),
                file_path=file_path,
                language=language,
                start_line=import_start or 1,
                end_line=import_end or 1,
                symbol_type="imports",
                symbol_name="imports",
                imports=file_imports,
                parser_used="tree_sitter",
            ))

        # Add module-level code that wasn't covered by any symbol
        all_lines = content.split("\n")
        uncovered_lines: list[tuple[int, str]] = []
        for line_idx, line_text in enumerate(all_lines):
            byte_pos = len("\n".join(all_lines[:line_idx]).encode("utf-8"))
            is_covered = any(start <= byte_pos < end for start, end in covered_ranges)
            if not is_covered and line_text.strip():
                uncovered_lines.append((line_idx + 1, line_text))

        if uncovered_lines:
            uncovered_text = "\n".join(text for _, text in uncovered_lines)
            if uncovered_text.strip():
                chunks.append(CodeChunk(
                    content=f"# File: {file_path}\n# Module-level code\n{uncovered_text}",
                    file_path=file_path,
                    language=language,
                    start_line=uncovered_lines[0][0],
                    end_line=uncovered_lines[-1][0],
                    symbol_type="module",
                    symbol_name="module_level",
                    imports=file_imports,
                    parser_used="tree_sitter",
                ))

        # Set chunk indices
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i

        return chunks


# ---------------------------------------------------------------------------
# Regex Chunker (Accuracy: MEDIUM — fallback)
# ---------------------------------------------------------------------------

# Python patterns
_PY_CLASS_RE = re.compile(r"^(class\s+(\w+).*?:)", re.MULTILINE)
_PY_FUNC_RE = re.compile(
    r"^((?:@\w+(?:\(.*?\))?\s*\n)*"  # decorators
    r"(?:async\s+)?def\s+(\w+)\s*\()",
    re.MULTILINE,
)
_PY_IMPORT_RE = re.compile(r"^(?:import|from)\s+", re.MULTILINE)

# JavaScript / TypeScript patterns
_JS_FUNC_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)",
    re.MULTILINE,
)
_JS_ARROW_RE = re.compile(
    r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
    re.MULTILINE,
)
_JS_CLASS_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)",
    re.MULTILINE,
)


class RegexChunker:
    """Regex-based chunking for when tree-sitter is unavailable.

    Uses indentation tracking (Python) or brace depth (JS/TS) to
    determine symbol boundaries and parent-child relationships.
    """

    MAX_CHUNK_SIZE = 3000

    def chunk(
        self,
        content: str,
        language: str,
        file_path: str,
    ) -> list[CodeChunk]:
        if language == "python":
            return self._chunk_python(content, file_path)
        elif language in ("javascript", "typescript"):
            return self._chunk_js_ts(content, file_path, language)
        return []  # Unsupported → fall through to line chunker

    def _chunk_python(self, content: str, file_path: str) -> list[CodeChunk]:
        lines = content.split("\n")
        chunks: list[CodeChunk] = []
        file_imports: list[str] = []

        # First pass: collect imports
        import_lines: list[str] = []
        import_start = None
        import_end = None
        for i, line in enumerate(lines):
            if _PY_IMPORT_RE.match(line):
                import_lines.append(line)
                if import_start is None:
                    import_start = i + 1
                import_end = i + 1
                match = re.search(r"(?:from|import)\s+([\w.]+)", line)
                if match:
                    file_imports.append(match.group(1).split(".")[0])

        if import_lines:
            chunks.append(CodeChunk(
                content="\n".join(import_lines),
                file_path=file_path,
                language="python",
                start_line=import_start or 1,
                end_line=import_end or 1,
                symbol_type="imports",
                symbol_name="imports",
                imports=list(set(file_imports)),
                parser_used="regex",
            ))

        # Second pass: find symbols using indentation-based scope tracking
        current_class: Optional[str] = None
        current_class_indent: int = -1
        symbol_start: Optional[int] = None
        symbol_lines: list[str] = []
        symbol_name = ""
        symbol_type = ""
        symbol_parent: Optional[str] = None

        def _flush_symbol():
            nonlocal symbol_start, symbol_lines, symbol_name, symbol_type, symbol_parent
            if symbol_lines and symbol_start is not None:
                text = "\n".join(symbol_lines)
                # Prepend class context for methods
                if symbol_parent:
                    text = f"# File: {file_path}\nclass {symbol_parent}:\n\n{text}"
                else:
                    text = f"# File: {file_path}\n{text}"

                meta = {
                    "file_path": file_path,
                    "language": "python",
                    "start_line": symbol_start,
                    "end_line": symbol_start + len(symbol_lines) - 1,
                    "symbol_type": symbol_type,
                    "symbol_name": symbol_name,
                    "parent_symbol": symbol_parent,
                    "parent_type": "class" if symbol_parent else None,
                    "imports": list(set(file_imports)),
                    "parser_used": "regex",
                }

                if len(text) > self.MAX_CHUNK_SIZE:
                    chunks.extend(_split_oversized(text, meta, self.MAX_CHUNK_SIZE))
                else:
                    chunks.append(CodeChunk(content=text, **meta))

            symbol_start = None
            symbol_lines = []
            symbol_name = ""
            symbol_type = ""
            symbol_parent = None

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            indent = len(line) - len(stripped) if stripped else 999

            # Check if we've exited the current class scope
            if current_class is not None and indent <= current_class_indent and stripped:
                if not stripped.startswith("@") and not stripped.startswith("#"):
                    current_class = None
                    current_class_indent = -1

            # Class definition
            class_match = re.match(r"^class\s+(\w+)", stripped)
            if class_match and indent == 0:
                _flush_symbol()
                current_class = class_match.group(1)
                current_class_indent = indent
                # Store class signature as its own chunk
                chunks.append(CodeChunk(
                    content=f"# File: {file_path}\n{line}",
                    file_path=file_path,
                    language="python",
                    start_line=i + 1,
                    end_line=i + 1,
                    symbol_type="class",
                    symbol_name=current_class,
                    imports=list(set(file_imports)),
                    parser_used="regex",
                ))
                continue

            # Function/method definition (including async, decorators)
            func_match = re.match(r"^(?:@\w+.*\n)*(?:async\s+)?def\s+(\w+)", stripped)
            if not func_match:
                func_match = re.match(r"^(?:async\s+)?def\s+(\w+)", stripped)

            if func_match:
                _flush_symbol()
                symbol_name = func_match.group(1)
                symbol_start = i + 1
                symbol_type = "method" if current_class else "function"
                symbol_parent = current_class
                symbol_lines = [line]
                continue

            # Continue accumulating current symbol
            if symbol_start is not None:
                # Check if this line is still part of the current symbol
                if stripped and indent <= (current_class_indent + 4 if current_class else 0) and not stripped.startswith("#"):
                    # New top-level statement — flush
                    if not stripped.startswith("@") and indent == (current_class_indent + 4 if current_class else 0):
                        # Could be next method at same level
                        next_func = re.match(r"^(?:async\s+)?def\s+", stripped)
                        if next_func:
                            _flush_symbol()
                            symbol_name = re.match(r"^(?:async\s+)?def\s+(\w+)", stripped).group(1)
                            symbol_start = i + 1
                            symbol_type = "method" if current_class else "function"
                            symbol_parent = current_class
                            symbol_lines = [line]
                            continue
                symbol_lines.append(line)

        _flush_symbol()

        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i

        return chunks

    def _chunk_js_ts(self, content: str, file_path: str, language: str) -> list[CodeChunk]:
        """Regex chunker for JavaScript/TypeScript using brace-depth tracking."""
        lines = content.split("\n")
        chunks: list[CodeChunk] = []
        file_imports: list[str] = []

        # Collect imports
        import_lines: list[str] = []
        import_start = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("const ") and "require(" in stripped:
                import_lines.append(line)
                if import_start is None:
                    import_start = i + 1
                match = re.search(r"from\s+['\"]([^'\"]+)['\"]", line)
                if match:
                    file_imports.append(match.group(1))

        if import_lines:
            chunks.append(CodeChunk(
                content="\n".join(import_lines),
                file_path=file_path,
                language=language,
                start_line=import_start or 1,
                end_line=(import_start or 1) + len(import_lines) - 1,
                symbol_type="imports",
                symbol_name="imports",
                imports=list(set(file_imports)),
                parser_used="regex",
            ))

        # Find function/class boundaries
        for match in _JS_FUNC_RE.finditer(content):
            name = match.group(1)
            start_pos = match.start()
            start_line = content[:start_pos].count("\n") + 1
            # Find matching closing brace
            end_line = self._find_brace_end(lines, start_line - 1)
            text = "\n".join(lines[start_line - 1:end_line])

            chunks.append(CodeChunk(
                content=f"// File: {file_path}\n{text}",
                file_path=file_path,
                language=language,
                start_line=start_line,
                end_line=end_line,
                symbol_type="function",
                symbol_name=name,
                imports=list(set(file_imports)),
                parser_used="regex",
            ))

        for match in _JS_CLASS_RE.finditer(content):
            name = match.group(1)
            start_pos = match.start()
            start_line = content[:start_pos].count("\n") + 1
            end_line = self._find_brace_end(lines, start_line - 1)
            text = "\n".join(lines[start_line - 1:end_line])

            chunks.append(CodeChunk(
                content=f"// File: {file_path}\n{text}",
                file_path=file_path,
                language=language,
                start_line=start_line,
                end_line=end_line,
                symbol_type="class",
                symbol_name=name,
                imports=list(set(file_imports)),
                parser_used="regex",
            ))

        for match in _JS_ARROW_RE.finditer(content):
            name = match.group(1)
            start_pos = match.start()
            start_line = content[:start_pos].count("\n") + 1
            end_line = self._find_brace_end(lines, start_line - 1)
            text = "\n".join(lines[start_line - 1:end_line])

            chunks.append(CodeChunk(
                content=f"// File: {file_path}\n{text}",
                file_path=file_path,
                language=language,
                start_line=start_line,
                end_line=end_line,
                symbol_type="function",
                symbol_name=name,
                imports=list(set(file_imports)),
                parser_used="regex",
            ))

        # Sort by start line, deduplicate overlapping chunks
        chunks_sorted = sorted(chunks, key=lambda c: c.start_line)
        for i, chunk in enumerate(chunks_sorted):
            chunk.chunk_index = i

        return chunks_sorted

    @staticmethod
    def _find_brace_end(lines: list[str], start_idx: int) -> int:
        """Find the line where a brace-delimited block closes."""
        depth = 0
        found_open = False
        for i in range(start_idx, len(lines)):
            for char in lines[i]:
                if char == "{":
                    depth += 1
                    found_open = True
                elif char == "}":
                    depth -= 1
                    if found_open and depth == 0:
                        return i + 1  # 1-indexed
        return len(lines)


# ---------------------------------------------------------------------------
# Line Chunker (Accuracy: LOW — final fallback)
# ---------------------------------------------------------------------------

class LineChunker:
    """Simple line-based chunking with configurable overlap.

    Used for languages without regex support or when both
    tree-sitter and regex fail.
    """

    def __init__(self, max_chunk_size: int = 1500, overlap_lines: int = 5):
        self.max_chunk_size = max_chunk_size
        self.overlap_lines = overlap_lines

    def chunk(
        self,
        content: str,
        language: str,
        file_path: str,
    ) -> list[CodeChunk]:
        lines = content.split("\n")
        chunks: list[CodeChunk] = []
        current_lines: list[str] = []
        current_size = 0
        chunk_start = 1

        for i, line in enumerate(lines):
            line_size = len(line) + 1
            if current_size + line_size > self.max_chunk_size and current_lines:
                chunks.append(CodeChunk(
                    content="\n".join(current_lines),
                    file_path=file_path,
                    language=language,
                    start_line=chunk_start,
                    end_line=chunk_start + len(current_lines) - 1,
                    symbol_type="module",
                    symbol_name=f"lines_{chunk_start}",
                    parser_used="line",
                ))
                # Overlap: keep last N lines
                overlap = current_lines[-self.overlap_lines:] if self.overlap_lines > 0 else []
                chunk_start = chunk_start + len(current_lines) - len(overlap)
                current_lines = list(overlap)
                current_size = sum(len(l) + 1 for l in current_lines)

            current_lines.append(line)
            current_size += line_size

        if current_lines:
            text = "\n".join(current_lines)
            if text.strip():
                chunks.append(CodeChunk(
                    content=text,
                    file_path=file_path,
                    language=language,
                    start_line=chunk_start,
                    end_line=chunk_start + len(current_lines) - 1,
                    symbol_type="module",
                    symbol_name=f"lines_{chunk_start}",
                    parser_used="line",
                ))

        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i

        return chunks


# ---------------------------------------------------------------------------
# Orchestrator: Tree-sitter → Regex → Line
# ---------------------------------------------------------------------------

_tree_sitter_chunker = TreeSitterChunker()
_regex_chunker = RegexChunker()
_line_chunker = LineChunker()


def chunk_code_file(
    content: str,
    file_path: str,
    max_chunk_size: int = 3000,
) -> list[CodeChunk]:
    """Chunk a source file using the best available strategy.

    Tries tree-sitter first, falls back to regex, then to line-based.
    """
    if not content or not content.strip():
        return []

    language = detect_language(file_path)

    # Layer 1: Tree-sitter (highest accuracy)
    chunks = _tree_sitter_chunker.chunk(content, language, file_path)
    if chunks:
        logger.debug("Tree-sitter produced %d chunks for %s", len(chunks), file_path)
        return chunks

    # Layer 2: Regex (medium accuracy)
    chunks = _regex_chunker.chunk(content, language, file_path)
    if chunks:
        logger.debug("Regex produced %d chunks for %s", len(chunks), file_path)
        return chunks

    # Layer 3: Line-based (universal fallback)
    chunks = _line_chunker.chunk(content, language, file_path)
    logger.debug("Line chunker produced %d chunks for %s", len(chunks), file_path)
    return chunks
