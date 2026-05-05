# mes - Multi-Engine Search CLI

**饕餮进化 R15 版本** | Python 3.11+ | 熔断降级 | 并行搜索

Forked and enhanced from [maxiee/MultiEngineSearch](https://github.com/maxiee/MultiEngineSearch)

---

## 概述

mes 是一个遵循 Unix 哲学的多搜索引擎命令行工具，支持熔断降级 (Circuit Breaker) 模式，确保高可靠搜索体验。

## 新特性 (R15 版本)

- ⚡ **熔断降级模式** — 引擎失败自动切换，唔会卡死
- 🔄 **并行搜索降级** — DuckDuckGo → Bing → Google，自动降级
- 🐍 **Python 3.11+ 支持** — 修复了 3.13+ 限制
- 🆕 **Bing 搜索引擎** — 新增 Bing API 支持
- 💾 **持久化熔断状态** — 熔断器状态跨会话保持

## 安装

```bash
# 方法1: pip install (推荐)
pip install git+https://github.com/Barefoothare-gif/mes.git

# 方法2: 从源码
git clone https://github.com/Barefoothare-gif/mes.git
cd mes
pip install -e .
```

**依赖:**
- Python >= 3.11
- ddgs >= 3.0.0 (DuckDuckGo)
- requests >= 2.31.0
- typer >= 0.12.0

## 快速开始

```bash
# 基本搜索
mes search "python教程"

# 熔断降级模式 (推荐!)
mes search "AI新闻" --fallback

# 指定引擎
mes search "机器学习" --engine google --limit 5

# 查看熔断器状态
mes circuit-status

# 重置熔断器
mes circuit-reset duckduckgo
```

## 熔断降级模式

使用 `--fallback` 启用熔断降级：

```bash
mes search "最新AI" --fallback --limit 10
```

**降级顺序:** DuckDuckGo → Bing → (失败则返回空)

**熔断阈值:**
| 引擎 | 失败次数 | 恢复超时 |
|------|----------|----------|
| duckduckgo | 3 | 60秒 |
| bing | 3 | 60秒 |
| google | 2 | 120秒 |

## 熔断器管理

```bash
# 查看所有熔断器状态
mes circuit-status

# 重置单个熔断器
mes circuit-reset duckduckgo

# 重置所有熔断器
mes circuit-reset all
```

## 熔断器状态说明

| 状态 | 含义 | 行为 |
|------|------|------|
| 🟢 CLOSED | 正常 | 流量通过 |
| 🔴 OPEN | 熔断 | 跳过执行 |
| 🟡 HALF_OPEN | 半开 | 尝试恢复 |

## 引擎配置

**环境变量:**
```bash
# Google API (可选)
export MES_GOOGLE_API_KEY=your_key
export MES_GOOGLE_SEARCH_ENGINE_ID=your_id

# Bing API (可选)
export MES_BING_API_KEY=your_key
```

**Bing API 申请:** https://portal.azure.com (搜索 "Bing Search API v7")

## 与原版对比

| 特性 | 原版 (maxiee) | R15 版本 (suisui) |
|------|---------------|------------------|
| Python 要求 | >= 3.13 | >= 3.11 |
| DuckDuckGo | duckduckgo-search | ddgs (新包名) |
| 熔断降级 | ❌ | ✅ |
| Bing 引擎 | ❌ | ✅ |
| 熔断器CLI | ❌ | ✅ |

## 架构

```
SearchEngine (ABC)
    ├── DuckDuckGoEngine
    ├── GoogleEngine
    └── BingEngine

SearchEngineFactory
    ├── create_engine()
    ├── register_engine()
    └── get_circuit_status()

CircuitBreaker
    ├── CLOSED → OPEN → HALF_OPEN
    └── 三状态自动切换

search_with_fallback()
    └── 并行降级搜索
```

## License

MIT (继承原版许可证)
