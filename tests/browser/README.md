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

## Visual review

```sh
CHROME_PATH=/usr/bin/google-chrome npm run review:visual --prefix tests/browser -- before
CHROME_PATH=/usr/bin/google-chrome npm run review:visual --prefix tests/browser -- after
```

The review reuses the regression fixtures to capture the dashboard, contracts,
daily analysis, option sentiment, expiries and watchlist editor in both themes.
The `before` stage reads frontend assets from Git HEAD; `after` uses working files.
It checks page overflow at 320, 360, 390, 430 and 1280 pixels. The `after` stage
also checks 48px primary touch targets, the calendar and the activation dialog.
Secondary calendar day cells retain a 48px height and fit seven columns.
It checks long text/numbers, incomplete and empty data, loading/warning states,
reduced motion and a minimum 4.5:1 contrast for shared text/background pairs.
Images and measurements are saved in the ignored `output/ui-redesign/` directory.
