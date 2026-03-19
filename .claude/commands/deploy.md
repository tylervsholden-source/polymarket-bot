Run the full deploy pipeline for the trading bot:

1. **Test**: Run `python run_tests.py` or `pytest tests/` — if any test fails, stop and report
2. **Lint**: Check for syntax errors with `python -m py_compile` on changed files
3. **Build**: Verify all imports resolve correctly by doing a dry-run import of main modules
4. **Deploy**:
   - Kill any running bot process (`tasklist | grep python` then `taskkill`)
   - Start the bot with `python main.py` in background
   - Wait 15 seconds for first cycle
5. **Verify**:
   - Check `data/status.json` for running=true and cycle > 0
   - Check logs for any ERROR or CRITICAL messages
   - Report bot status: capital, open positions, last cycle time

If any step fails, stop immediately and report the failure with full context.
