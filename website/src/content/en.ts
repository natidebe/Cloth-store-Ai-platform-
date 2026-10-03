/**
 * The website's text in English (docs/website-content.md, as designed).
 * am.ts must have exactly the same pieces (the compiler checks).
 */
export const en = {
  meta: {
    title: 'StoreFront.et: your shop on Telegram, open 24 hours',
    description:
      'Customers order from your Telegram channel, the bot takes the order, your staff get it, your stock updates itself. Built in Ethiopia.',
  },
  nav: {
    how: 'How it works',
    features: 'Features',
    pricing: 'Pricing',
    faq: 'FAQ',
    start: 'Start free',
    language: 'Language',
    skip: 'Skip to content',
  },
  hero: {
    title: 'Your shop, open 24 hours with AI.',
    body: 'Customers order straight from your channel. The bot takes the order in Amharic or English, your staff get it in their group, and your stock updates itself.',
    start: 'Start free in Telegram',
    how: 'See how it works',
    checks: ['1 week free', 'No app to install', 'Built in Ethiopia'],
    shots: {
      channel:
        'A shop’s Telegram channel: a post with photo, price, colors and sizes, and the Order button',
      products: 'The shop dashboard in Telegram: products with stock and a low-stock warning',
      analytics:
        'The shop dashboard in Telegram: this week’s sales, Telegram vs in the shop, discounts by staff',
    },
  },
  problems: {
    title: "Selling on Telegram shouldn't take your whole day.",
    items: [
      {
        title: 'The same questions, all day.',
        body: 'Price? Size? Is it available? You answer one by one, and the rest wait.',
      },
      {
        title: 'Night messages become lost sales.',
        body: 'By morning, that customer bought somewhere else.',
      },
      {
        title: "You don't know what's left.",
        body: 'The channel says "available", the shelf is empty.',
      },
      {
        title: 'Sales in the shop are invisible.',
        body: 'Who sold what, at what price, with what discount?',
      },
    ],
  },
  steps: {
    title: 'Up and running in one afternoon.',
    items: [
      {
        title: 'Create your shop.',
        body: 'Open @{bot} in Telegram, name your shop and connect your own bot from @BotFather. About 5 minutes.',
      },
      {
        title: 'Connect your staff group and channel.',
        body: 'Add your bot and send one code. Group admins become owners, members become staff. No passwords to share.',
      },
      {
        title: 'Add your products.',
        body: 'Photo, price and stock, by color and size, or just one number for bags, belts and jewelry. Post to your channel with one tap.',
      },
      {
        title: 'Customers order.',
        body: 'They tap 🛒 Order on a post, choose, and confirm. The order arrives in your staff group, and when stock runs out the post shows SOLD OUT by itself.',
      },
    ],
  },
  features: {
    title: "Everything a Telegram shop needs. Nothing it doesn't.",
    groups: [
      {
        title: 'For your customers',
        items: [
          {
            title: 'Order in Amharic or English.',
            body: 'The bot speaks their language, with buttons instead of typing.',
          },
          {
            title: "Only what's really in stock.",
            body: 'Sold-out sizes are never offered, and items are held for 5 minutes while they order.',
          },
          {
            title: 'Pickup or delivery.',
            body: 'Delivery areas and fees are shown; delivery is paid when the items arrive.',
          },
          {
            title: 'Pay the way they already do.',
            body: 'Telebirr, CBE or any account you list.',
          },
        ],
      },
      {
        title: 'For your staff',
        items: [
          {
            title: 'Orders land in your staff group.',
            body: 'Everything the customer chose, their name and phone, ready to confirm.',
          },
          {
            title: 'Confirm payments in one tap.',
            body: 'Customers send the screenshot; staff check the money arrived and confirm.',
          },
          {
            title: 'Sell in the shop too.',
            body: 'Walk-in customers are recorded from the phone: the stock goes down and the channel post updates.',
          },
          {
            title: 'Discounts with limits.',
            body: 'Staff can lower the price only as far as you allow. The listed price never changes.',
          },
        ],
      },
      {
        title: 'For you, the owner',
        items: [
          {
            title: 'A dashboard inside Telegram.',
            body: 'Products, stock, orders and settings, on your phone. Nothing to install.',
          },
          {
            title: 'Know your numbers.',
            body: 'Sales today, this week, this month; Telegram vs in the shop; discounts given, by staff member; best-selling products.',
          },
          {
            title: 'Stock that keeps itself right.',
            body: 'Every order and shop sale updates the stock and the channel post. Low stock is flagged.',
          },
          {
            title: 'Your shop, your bot.',
            body: "Customers talk to your shop's own bot, with your name.",
          },
        ],
      },
    ],
    control: {
      title: 'Smart, but you stay in control',
      before: "The AI only helps the bot understand messages written in the customer's own words. ",
      strong: 'Prices, stock and payments always come from you.',
      after:
        " When the bot isn't sure, or a customer wants to bargain or complain, it hands the chat to your staff.",
    },
  },
  demo: {
    title: 'Order from our demo shop. It takes a minute.',
    body: 'Open the demo channel, tap 🛒 Order on any post and go through it as a customer would. Nothing is charged.',
    open: 'Open the demo shop',
    video: 'Or watch the 60-second video',
    videoSoon: 'The video is coming soon',
    videoFlow: 'post → Order → staff group',
  },
  audience: {
    title: 'Made for shops that sell things you can hold.',
    items: [
      { title: 'Boutique', body: 'Clothing, shoes, bags, belts & jewelry' },
      { title: 'Cosmetics', body: 'Makeup & perfume' },
      { title: 'Electronics', body: 'Phone accessories' },
      { title: 'Gifts', body: 'Gifts & home decor' },
    ],
    other: 'Selling something else? Tell us:',
  },
  pricing: {
    title: 'Simple prices, in birr.',
    popular: 'Most shops choose this',
    start: 'Start free',
    currency: 'ETB',
    plans: [
      {
        name: 'Free trial',
        price: '0',
        unit: 'for 1 week',
        for: 'Trying everything',
        includes: 'Everything in Pro',
      },
      {
        name: 'Basic',
        price: '1,500',
        unit: '/ month',
        for: 'Small shops',
        includes: 'Your bot, channel posts, staff group, dashboard, in-shop sales, analytics',
      },
      {
        name: 'Pro',
        price: '3,000',
        unit: '/ month',
        for: 'Busy shops with several staff',
        includes:
          'Everything in Basic, more AI replies, priority support, help adding your products',
      },
    ],
    notes: [
      {
        label: 'Founding shops:',
        text: 'the first 10 shops pay 1,000 ETB / month, for as long as they stay.',
      },
      { label: 'Yearly:', text: 'pay 10 months, get 12.' },
      { label: 'Pay with:', text: 'Telebirr or bank transfer (CBE and others).' },
    ],
  },
  trust: {
    title: 'Built in Addis Ababa, for Ethiopian shops.',
    people: [
      { name: 'Abdisa', role: 'Developer, Addis Ababa' },
      { name: 'Natnael', role: 'Developer, Addis Ababa' },
    ],
    data: {
      title: 'Your data stays yours.',
      body: 'Your products, orders and customers belong to your shop. We never sell or share them.',
    },
    reliable: {
      title: 'Reliable.',
      body: 'Monitored around the clock.',
      status: 'See the live status:',
      link: 'Status',
    },
    person: { title: 'A real person to talk to.', after: 'on Telegram.' },
  },
  faq: {
    title: 'Questions',
    items: [
      { q: 'Do I need a computer?', a: 'No. Everything works on your phone, inside Telegram.' },
      {
        q: 'Do my customers need to install anything?',
        a: 'No. They just use Telegram, as they do today.',
      },
      {
        q: 'Can I use my existing channel?',
        a: "Yes. Add your shop's bot to it and send the link code. Your subscribers stay.",
      },
      {
        q: "What if the bot doesn't understand a customer?",
        a: 'It passes the chat to your staff group, so a person answers.',
      },
      {
        q: 'Can customers bargain?',
        a: 'In the chat, bargaining goes to your staff. In the shop, staff can give a discount within the limit you set.',
      },
      {
        q: 'Who confirms that a customer paid?',
        a: 'Your staff, after checking the money arrived in Telebirr or the bank. The bot never confirms payments by itself.',
      },
      {
        q: 'I sell bags and jewelry, not sizes. Does it work?',
        a: 'Yes. Colors and sizes are optional; a product can be just one stock number.',
      },
      {
        q: 'How do I add staff?',
        a: 'Add them to your staff group. Group admins are owners, members are staff.',
      },
      {
        q: 'What happens if I stop paying?',
        a: 'Your shop is paused (the bot stops taking orders) and your data is kept for 30 days, so you can come back.',
      },
      {
        q: 'Is it in Amharic?',
        a: 'Yes: the bot, the posts and the dashboard, in Amharic and English.',
      },
    ],
  },
  cta: {
    title: 'Let your shop sell while you sleep.',
    body: 'Start free for 1 week. Set up in an afternoon.',
    start: 'Start free in Telegram',
    talk: 'Talk to us',
  },
  footer: {
    tagline: 'StoreFront.et: Telegram shops for Ethiopia',
    support: 'Support:',
    guide: 'Guide for shop owners',
    pricing: 'Pricing',
    privacy: 'Privacy',
    terms: 'Terms',
    status: 'Status',
    copyright: '© 2026 StoreFront.et. Made in Addis Ababa.',
  },
};

export type Content = typeof en;
export type Lang = 'am' | 'en';
