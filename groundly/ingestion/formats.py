"""Format tables shared by the pipeline and the extraction worker. Stdlib-only:
extract_worker imports this at module load, before it decides whether it even
needs docling — pulling in a heavier module here would defeat that."""

DOCLING_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".md": "md",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".tex": "latex",
    ".latex": "latex",
    ".adoc": "asciidoc",
    ".asciidoc": "asciidoc",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".epub": "epub",
}
DOCLING_SUFFIXES = set(DOCLING_FORMATS)
# Everything else on the pipeline allowlist (txt + source code) is read as plain
# text and chunked by token windows — docling's converter does not accept it.
PLAIN_TEXT_SUFFIXES = {
    ".txt",
    ".py",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".ts",
    ".rs",
    ".go",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".sql",
    ".cs",
    ".rb",
    ".kt",
    ".swift",
}
SUPPORTED_SUFFIXES = DOCLING_SUFFIXES | PLAIN_TEXT_SUFFIXES
