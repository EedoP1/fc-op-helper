# Technology Stack

**Analysis Date:** 2026-03-25

## Languages

**Primary:**
- Python 3.12 - Core application language

## Runtime

**Environment:**
- Python 3.12.10 - Installed runtime

**Package Manager:**
- pip - Dependency management
- Lockfile: requirements.txt (present)

## Frameworks

**Core:**
- httpx 0.28.1 - Async HTTP client for API requests
- pydantic 2.12.5 - Data validation and modeling
- click 8.3.1 - CLI command framework
- rich 14.3.3 - Terminal output formatting and display

**Testing:**
- pytest 9.0.2 - Test runner
- pytest-asyncio 1.3.0 - Async test support

**Build/Dev:**
- None detected (no build framework required)

## Key Dependencies

**Critical:**
- httpx 0.28.1 - Async HTTP client for fut.gg API calls, handles connection pooling and retry logic
- pydantic 2.12.5 - Data model validation for Player, PlayerMarketData, SaleRecord, PricePoint types

**Infrastructure:**
- click 8.3.1 - CLI argument parsing and command structure
- rich 14.3.3 - Colored table rendering, progress panels, terminal formatting for output

## Configuration

**Environment:**
- Python virtual environment (.venv directory present)
- Configuration constants hardcoded in `src/config.py` (EA_TAX_RATE, TARGET_PLAYER_COUNT)
- No environment file required for operation

**Build:**
- No build configuration (pure Python, no compilation step)

## Platform Requirements

**Development:**
- Python 3.12+
- pip for dependency installation
- No OS-specific dependencies (cross-platform compatible)

**Production:**
- Python 3.12 runtime
- Network access to https://www.fut.gg API endpoints
- ~30-second timeout for API requests
- Async I/O support (requires modern Python asyncio capability)

---

*Stack analysis: 2026-03-25*
