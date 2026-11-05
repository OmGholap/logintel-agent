# Contributing

Pull requests are welcome. `main` only changes through pull requests.

1. Fork the repo and create a branch.
2. Install and run the tests (no API key or Loki needed, the model and Loki are faked):
   ```bash
   pip install -e ".[dev]"
   pytest
   ```
3. Keep the change small and add a test for it. If you change a prompt or the answer path, say which of the questions in `golden_queries.md` you re-ran against the real model.
4. Open a pull request and fill in the template.

Ground rules:

- The agent is **read-only**: it must never write to the logs.
- Citations must stay real: every cited line has to be a line the model was shown, copied by code.
- Do not commit keys, `.env` files, or real log data. The demo logs in `demo_data/` are synthetic and must stay that way.
