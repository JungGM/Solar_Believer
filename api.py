"""
api.py  ─  K-VPP Insight 루트 진입점
실행: python api.py --start 2022-01-01 --end 2024-12-31
"""

if __name__ == "__main__":
    import sys

    if "--kma-hub" in sys.argv:
        from scripts.collect_kma_hub import main

        sys.argv.remove("--kma-hub")
    elif "--continuous" in sys.argv:
        from scripts.maintain_public_data import main

        sys.argv.remove("--continuous")
    elif "--approved-history" in sys.argv:
        from scripts.collect_public_history import main

        sys.argv.remove("--approved-history")
    else:
        from API.api_collector import main

    raise SystemExit(main())

