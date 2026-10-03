import { CheckSquare } from 'lucide-react';

import analytics from '@/assets/screens/analytics.webp';
import channel from '@/assets/screens/channel.webp';
import orders from '@/assets/screens/orders.webp';
import products from '@/assets/screens/products.webp';
import { useContent } from '@/content';

import s from './showcase.module.css';

/*
 * The hero: real screenshots as phones in 3D. In front, the Mini App's
 * Products dashboard; behind it, a post in the shop's channel with the Order
 * button and the Analytics; floating in front, the order arriving in the
 * staff group.
 */

const SCREEN = { width: 540, height: 1097 }; // the WebP files (src/assets/screens)

function Phone({ src, alt, place }: { src: string; alt: string; place: string | undefined }) {
  return (
    <figure className={`${s.phone} ${place ?? ''}`}>
      <img
        className={s.screen}
        src={src}
        alt={alt}
        width={SCREEN.width}
        height={SCREEN.height}
        decoding="async"
      />
      <span className={s.glare} aria-hidden="true" />
    </figure>
  );
}

export function HeroShowcase() {
  const t = useContent().hero.shots;
  return (
    <div className={s.stage}>
      <span className={s.glow} aria-hidden="true" />
      <div className={s.scene}>
        <Phone src={channel} alt={t.channel} place={s.left} />
        <Phone src={analytics} alt={t.analytics} place={s.right} />
        <Phone src={products} alt={t.products} place={s.front} />
        <OrderCard />
      </div>
      <span className={s.floor} aria-hidden="true" />
    </div>
  );
}

const CARD_SCREENS = { orders, analytics };

/** A feature card's phone, turned in 3D: staff (Orders) and owner (Analytics). */
export function CardPhone({ screen }: { screen: keyof typeof CARD_SCREENS }) {
  const t = useContent().hero.shots;
  return (
    <div className={s.cardStage}>
      <Phone src={CARD_SCREENS[screen]} alt={t[screen]} place={s.cardPhone} />
    </div>
  );
}

/** The staff group's message for an order from that post (decorative). */
function OrderCard() {
  return (
    <div className={s.orderCard} aria-hidden="true">
      <div className={s.orderFrom}>
        <span className={s.online} /> Betty fashion · Staff
      </div>
      <div className={s.orderTitle}>🛍 New order #1042</div>
      <div className={s.orderLine}>550 (New balance) · White · 43 × 1</div>
      <div className={s.orderLine}>Delivery · Bole · 5,000 ETB</div>
      <span className={s.pill}>Payment screenshot received</span>
      <div className={s.orderActions}>
        <span className={s.confirm}>
          <CheckSquare size={16} /> Confirm
        </span>
        <span className={s.reject}>Reject</span>
      </div>
    </div>
  );
}
