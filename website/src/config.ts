/**
 * Everything that links somewhere. Change it here; the whole site follows.
 * A link set to null hides what needs it (e.g. no demo channel yet: no
 * "Open the demo shop" button), so the site never has a dead link.
 */
export const site = {
  name: 'StoreFront.et',
  /**
   * The platform bot shop owners sign up in. @StoreFrontETbot doesn't exist
   * yet: switch to it once it's created (and set as PLATFORM_BOT_TOKEN on Render).
   */
  platformBot: 'Platform_16bot',
  /** Customer support on Telegram (the first one gets the "Talk to us" button). */
  support: ['kiyay30', 'AWGKGGK'],
  /** Support phone numbers, shown in the footer. */
  phones: ['+251960570692', '+251967026271'],
  /** A public demo shop channel visitors can order from. */
  demoUrl: null as string | null,
  /** The 60-second video (an .mp4 in public/, or a full URL). */
  videoUrl: null as string | null,
  /** The UptimeRobot public status page. */
  statusUrl: null as string | null,
  guideUrl: null as string | null,
  privacyUrl: null as string | null,
  termsUrl: null as string | null,
};

/** "Start free": opens the platform bot, tagged as coming from the website. */
export const startLink = `https://t.me/${site.platformBot}?start=web`;
export const supportLink = `https://t.me/${site.support[0]}`;
