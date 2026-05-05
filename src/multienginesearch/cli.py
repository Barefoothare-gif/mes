"""
Multi-Engine Search CLI - 饕餮进化 R15 版本
支持: 熔断降级, 并行搜索, Bing引擎
"""

import typer
from typing import Optional
from typing_extensions import Annotated
from .engines import (
    SearchEngineFactory, 
    format_results, 
    search_with_fallback,
    SEARCH_CIRCUIT_BREAKERS
)

app = typer.Typer(
    name="mes",
    help="Multi-Engine Search - 多引擎搜索工具 (熔断降级版)",
    add_completion=False,
    rich_markup_mode="markdown",
)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="搜索查询字符串")],
    engine: Annotated[
        Optional[str],
        typer.Option("--engine", "-e", help="指定搜索引擎 (duckduckgo, google, bing)"),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-l", help="返回结果数量限制", min=1, max=100)
    ] = 10,
    output: Annotated[
        Optional[str], typer.Option("--output", "-o", help="输出格式 (json, simple)")
    ] = "simple",
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="显示详细信息")
    ] = False,
    time: Annotated[
        Optional[str],
        typer.Option(
            "--time", "-t",
            help="时间筛选范围 (d=最近一天, w=最近一周, m=最近一月, y=最近一年)",
        ),
    ] = None,
    fallback: Annotated[
        bool,
        typer.Option("--fallback", "-f", help="启用熔断降级模式，失败时自动切换引擎")
    ] = False,
):
    """
    执行多引擎搜索 (支持熔断降级)
    
    **示例用法:**
    
    - `mes search "python tutorial"`
    - `mes search "机器学习" --engine google --limit 5`
    - `mes search "AI新闻" --output json --verbose`
    - `mes search "最新技术" --time d --limit 10`
    - `mes search "AI新闻" --fallback`  # 启用熔断降级
    """
    # 验证时间筛选参数
    if time and time not in ["d", "w", "m", "y"]:
        typer.echo(
            "❌ 无效的时间筛选参数。支持的选项: d (一天), w (一周), m (一月), y (一年)"
        )
        raise typer.Exit(1)
    
    if verbose:
        typer.echo(f"正在搜索: {query}")
        typer.echo(f"搜索引擎: {engine or '默认 (DuckDuckGo)'}")
        typer.echo(f"结果限制: {limit}")
        typer.echo(f"输出格式: {output}")
        if time:
            time_labels = {"d": "最近一天", "w": "最近一周", "m": "最近一月", "y": "最近一年"}
            typer.echo(f"时间筛选: {time_labels.get(time, time)}")
        if fallback:
            typer.echo("⚡ 熔断降级: 已启用")
    
    # 使用熔断降级模式
    if fallback:
        if verbose:
            typer.echo("🔄 使用熔断降级模式搜索...")
        
        engines = ["duckduckgo", "bing"] if not engine else [engine]
        response = search_with_fallback(query, engines=engines, limit=limit, time_filter=time)
        
        if not response.results:
            typer.echo("❌ 所有引擎均失败，请检查熔断器状态")
            typer.echo(f"错误信息: 可用 --circuit-status 查看熔断器状态")
            return
        
        formatted_results = format_results(response, output or "simple")
        typer.echo(formatted_results)
        return
    
    # 单引擎模式 (原有逻辑)
    engine_name = engine or "duckduckgo"
    search_engine = SearchEngineFactory.create_engine(engine_name)
    
    if not search_engine:
        available_engines = SearchEngineFactory.get_available_engines()
        typer.echo(f"❌ 不支持的搜索引擎: {engine_name}")
        typer.echo(f"💡 可用的搜索引擎: {', '.join(available_engines)}")
        raise typer.Exit(1)
    
    if verbose:
        typer.echo(f"🔍 正在使用 {search_engine.name} 搜索...")
    
    response = search_engine.search(query, limit, time_filter=time)
    
    if not response.results:
        typer.echo("❌ 没有找到搜索结果")
        return
    
    formatted_results = format_results(response, output or "simple")
    typer.echo(formatted_results)


@app.command()
def circuit_status():
    """
    查看所有熔断器状态
    
    **示例用法:**
    
    - `mes circuit-status`
    """
    typer.echo("⚡ 熔断器状态:\n")
    
    status = SearchEngineFactory.get_circuit_status()
    
    for name, info in status.items():
        state_emoji = {
            "CLOSED": "🟢",
            "OPEN": "🔴",
            "HALF_OPEN": "🟡",
        }.get(info["state"], "⚪")
        
        typer.echo(f"{state_emoji} {name}: {info['state']}")
        typer.echo(f"    失败次数: {info['failures']}/{info['failures']} (阈值: {info['failure_threshold']})")
        
        if info["last_failure"]:
            from datetime import datetime
            last_fail = datetime.fromtimestamp(info["last_failure"])
            typer.echo(f"    上次失败: {last_fail.strftime('%Y-%m-%d %H:%M:%S')}")
        
        typer.echo(f"    恢复超时: {info['recovery_timeout']}秒")
        typer.echo()


@app.command()
def circuit_reset(
    engine: Annotated[
        str,
        typer.Argument(help="要重置的搜索引擎名称 (duckduckgo, google, bing)")
    ] = None,
):
    """
    重置熔断器
    
    **示例用法:**
    
    - `mes circuit-reset duckduckgo`
    - `mes circuit-reset all`
    """
    if engine == "all":
        for name in SEARCH_CIRCUIT_BREAKERS:
            SearchEngineFactory.reset_circuit(name)
        typer.echo("✅ 所有熔断器已重置")
    elif engine:
        SearchEngineFactory.reset_circuit(engine)
        typer.echo(f"✅ {engine} 熔断器已重置")
    else:
        typer.echo("❌ 请指定要重置的引擎名称，或使用 'all' 重置所有")


@app.command()
def config(
    list_engines: Annotated[
        bool, typer.Option("--list", "-l", help="列出所有可用的搜索引擎")
    ] = False,
    set_default: Annotated[
        Optional[str], typer.Option("--set-default", help="设置默认搜索引擎")
    ] = None,
):
    """
    配置搜索引擎和设置
    
    **示例用法:**
    
    - `mes config --list`
    """
    if list_engines:
        typer.echo("📋 可用的搜索引擎:\n")
        engines = SearchEngineFactory.get_available_engines()
        for engine in engines:
            cb = SEARCH_CIRCUIT_BREAKERS.get(engine)
            state = cb.state if cb else "UNKNOWN"
            typer.echo(f"  • {engine} (熔断状态: {state})")
        
        typer.echo("\n💡 使用 --fallback 启用熔断降级模式")
    
    if set_default:
        available_engines = SearchEngineFactory.get_available_engines()
        if set_default in available_engines:
            typer.echo(f"✅ 已设置默认搜索引擎为: {set_default}")
        else:
            typer.echo(f"❌ 不支持的搜索引擎: {set_default}")
            typer.echo(f"💡 可用的搜索引擎: {', '.join(available_engines)}")


@app.command()
def version():
    """
    显示版本信息
    """
    typer.echo("🔍 Multi-Engine Search (mes) v0.2.0")
    typer.echo("   饕餮进化 R15 版本 - 熔断降级 + Python 3.11+ 支持")
    typer.echo("   Forked from maxiee/MultiEngineSearch")


def main():
    """CLI 入口点"""
    app()


if __name__ == "__main__":
    main()
