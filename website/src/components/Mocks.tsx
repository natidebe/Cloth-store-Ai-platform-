import { Check, CheckSquare, Minus, Plus, ShoppingBag, X } from 'lucide-react';

import s from './mocks.module.css';

/*
 * Pictures of the product, drawn in HTML (sharp on every screen, a few bytes).
 * They show the app's own English labels, as in the design. They're
 * decorative: the text next to each says the same, so screen readers skip them.
 */

function PhotoBox({ height }: { height: number }) {
  return (
    <div className={s.photo} style={{ height }}>
      <ShoppingBag size={Math.round(height / 4)} strokeWidth={1.4} />
    </div>
  );
}

const ORDER = '🛒 እዘዝ / Order';

/** The hero: a channel post on a phone, and the order arriving in the staff group. */
export function HeroMock() {
  return (
    <div className={s.hero} aria-hidden="true">
      <div className={s.phone}>
        <div className={s.channelHead}>
          <span className={s.avatar}>S</span>
          <span>
            <span className={s.channelName}>Selam Shoes</span>
            <span className={s.channelSub}>channel</span>
          </span>
        </div>
        <div className={s.chat}>
          <div className={s.post}>
            <PhotoBox height={220} />
            <div className={s.postBody}>
              <div className={s.postTitle}>Leather loafers · የቆዳ ጫማ</div>
              <div className={s.postPrice}>2,400 ብር</div>
              <div className={s.postLine}>
                <span className={s.dot} style={{ background: '#111114' }} /> Black ·{' '}
                <span className={s.dot} style={{ background: '#6b3f26' }} /> Brown
              </div>
              <div className={s.postLine}>39 · 40 · 41 · 42 · 43</div>
            </div>
          </div>
          <div className={s.orderButton}>{ORDER}</div>
        </div>
      </div>

      <div className={s.orderCard}>
        <div className={s.orderFrom}>
          <span className={s.online} /> Selam Shoes · Staff
        </div>
        <div className={s.orderTitle}>🛍 New order #1042</div>
        <div className={s.orderLine}>Leather loafers · Black · 41 × 1</div>
        <div className={s.orderLine}>Delivery · Bole · 2,400 ብር</div>
        <span className={s.pill}>Payment screenshot received</span>
        <div className={s.orderActions}>
          <span className={s.confirm}>
            <CheckSquare size={16} /> Confirm
          </span>
          <span className={s.reject}>Reject</span>
        </div>
      </div>
    </div>
  );
}

/** Step 1: Create store. */
export function CreateMock() {
  return (
    <div className={s.screen} aria-hidden="true">
      <div className={s.screenTitle}>Create store</div>
      <div className={s.label}>Shop name</div>
      <div className={s.field}>Selam Shoes</div>
      <div className={s.label}>Bot token from @BotFather</div>
      <div className={`${s.field} ${s.fieldFaint}`}>7412…:AAH•••</div>
      <div className={s.blueButton}>Create</div>
    </div>
  );
}

/** Step 2: Connect with a /link code. */
export function ConnectMock() {
  return (
    <div className={s.screen} aria-hidden="true">
      <div className={s.screenTitle}>Connect</div>
      <div className={s.label}>Send this in your staff group and your channel:</div>
      <div className={s.code}>/link 4821</div>
      <div className={s.connectLine}>
        <Check size={16} className={s.tick} /> Staff group connected
      </div>
      <div className={s.connectLine}>
        <span className={s.circle} /> Channel: waiting
      </div>
    </div>
  );
}

const GRID = [
  { color: 'Black', cells: [4, 6, 1, 3] },
  { color: 'Brown', cells: [2, 0, 5, 2] },
];

/** Step 3: the stock grid, and a bag with one counter. */
export function StockMock() {
  return (
    <div className={s.stack} aria-hidden="true">
      <div className={s.screen}>
        <div className={s.screenTitle}>Leather loafers</div>
        <div className={s.grid}>
          <span />
          {['39', '40', '41', '42'].map((size) => (
            <span key={size} className={s.gridHead}>
              {size}
            </span>
          ))}
          {GRID.map((row) => (
            <Row key={row.color} color={row.color} cells={row.cells} />
          ))}
        </div>
      </div>
      <div className={`${s.screen} ${s.bagRow}`}>
        <span className={s.screenTitle} style={{ margin: 0 }}>
          Leather bag
        </span>
        <span className={s.stepper}>
          <span className={s.stepButton}>
            <Minus size={12} />
          </span>
          <b>7</b>
          <span className={s.stepButton}>
            <Plus size={12} />
          </span>
        </span>
      </div>
      <div className={s.blueButton}>Post to channel</div>
    </div>
  );
}

function Row({ color, cells }: { color: string; cells: number[] }) {
  return (
    <>
      <span className={s.gridColor}>{color}</span>
      {cells.map((stock, index) => (
        <span
          key={index}
          className={`${s.cell} ${stock === 0 ? s.cellOut : stock === 1 ? s.cellLow : ''}`}
        >
          {stock}
        </span>
      ))}
    </>
  );
}

/** Step 4: the post sold out, the Order button, the order in the group. */
export function PostMock() {
  return (
    <div className={`${s.stack} ${s.chatPanel}`} aria-hidden="true">
      <div className={s.post}>
        <PhotoBox height={70} />
        <div className={s.soldRow}>
          <span className={s.soldLabel}>
            Brown ·<br />
            40
          </span>
          <span className={s.sold}>
            <X size={15} strokeWidth={3} /> ተሽጧል / SOLD OUT
          </span>
        </div>
      </div>
      <div className={s.orderButtonSmall}>{ORDER}</div>
      <div className={s.screen}>
        <div className={s.newOrder}>🛍 New order #1043 → Staff group</div>
        <div className={s.faint}>Black · 41 × 1 · Pickup</div>
      </div>
    </div>
  );
}

/** The owner card: this week's numbers. */
export function DashboardMock() {
  return (
    <div className={s.dashboard} aria-hidden="true">
      <div className={s.dashTitle}>📊 Dashboard · This week</div>
      <div className={s.bar}>
        <span style={{ width: '64%', background: 'var(--blue)' }} />
        <span style={{ width: '36%', background: 'var(--yellow)' }} />
      </div>
      <div className={s.dashSplit}>
        <span>Telegram 64%</span>
        <span>🏪 In shop 36%</span>
      </div>
      <div className={s.dashLabel}>Discounts by staff</div>
      <div className={s.dashRow}>
        <span>Hana</span>
        <b>320 ብር</b>
      </div>
      <div className={s.dashRow}>
        <span>Dawit</span>
        <b>150 ብር</b>
      </div>
      <div className={s.dashNote}>Demo data</div>
    </div>
  );
}
