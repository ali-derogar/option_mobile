# Browser regression tests

Requires Node.js 20 or newer. Dependencies are used only by tests.

```sh
npm ci --prefix tests/browser
npx --prefix tests/browser playwright install chromium
npm test --prefix tests/browser
```

To use an installed Chrome instead of downloading Chromium:

```sh
CHROME_PATH=/usr/bin/google-chrome npm test --prefix tests/browser
```

Tests serve the real frontend through intercepted requests and deterministic API
fixtures. They never contact the market API or modify the application's database.
