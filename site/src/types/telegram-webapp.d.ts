// Minimal shape of the bits of Telegram's WebApp SDK this dashboard actually uses.
// https://core.telegram.org/bots/webapps
export interface TelegramWebApp {
  initData: string;
  ready(): void;
  expand(): void;
  colorScheme: "light" | "dark";
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

export {};
