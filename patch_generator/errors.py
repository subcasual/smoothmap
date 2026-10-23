class GeneratorError(Exception):
    """The edits can't be turned into a patch set (yet), or FFDec is missing or failed. Nothing is written."""
