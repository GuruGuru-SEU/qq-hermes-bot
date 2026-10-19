def register(ctx):
    from .qqhermes.adapter import register
    from .qqhermes.tools import register_tools
    register_tools(ctx)
    register(ctx)
