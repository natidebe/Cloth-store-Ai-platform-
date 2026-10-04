import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useAnalytics, useOrders } from '@/api/queries';
import type { Order, OrderFilter } from '@/api/types';
import { LoadFailed } from '@/app/AccessScreens';
import { useLanguage, useStore } from '@/app/hooks';
import { StoreTabs } from '@/app/StoreShell';
import { Icon } from '@/components/Icon';
import {
  Badge,
  BottomBar,
  Button,
  Centered,
  Chips,
  Page,
  PageHeader,
  Sheet,
  SkeletonList,
} from '@/components/ui';
import { money, orderTime, variantLabel } from '@/lib/format';
import { openTelegramLink } from '@/lib/telegram';
import { percentOff } from '@/state/counterDraft';

import s from './orders.module.css';

/** Design: "Orders with 🏪 mark" / "Orders, Amharic". */
export function OrdersScreen() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { storeId, role, store } = useStore();
  const [filter, setFilter] = useState<OrderFilter>('all');
  const [open, setOpen] = useState<Order | null>(null);
  const orders = useOrders(storeId, filter);
  const list = orders.data?.pages.flatMap((page) => page.orders) ?? [];
  const noOrdersAtAll = filter === 'all' && orders.isSuccess && list.length === 0;

  if (orders.isError && !orders.data) {
    return <LoadFailed error={orders.error} onRetry={() => void orders.refetch()} />;
  }

  const shareShop = () => {
    if (!store.bot_username) return;
    const link = `https://t.me/${store.bot_username}`;
    openTelegramLink(
      `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(store.name)}`,
    );
  };

  return (
    <Page>
      <PageHeader title={t('orders.title')} role={role} />
      <StoreTabs />

      {noOrdersAtAll ? (
        <Centered
          icon="box"
          title={t('orders.emptyTitle')}
          body={t('orders.emptyBody')}
          action={
            store.bot_username ? (
              <Button variant="soft" icon="send" onClick={shareShop}>
                {t('orders.shareLink')}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <TodayLink onOpen={() => navigate(`/s/${storeId}/analytics`)} />
          <Chips
            label={t('orders.title')}
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: t('orders.filterAll') },
              { value: 'telegram', label: t('orders.filterTelegram') },
              { value: 'in_shop', label: `🏪 ${t('orders.filterInShop')}` },
            ]}
          />
          {orders.isPending ? (
            <SkeletonList rows={4} />
          ) : list.length === 0 ? (
            <p className={s.muted} style={{ textAlign: 'center' }}>
              {t('analytics.noSales')}
            </p>
          ) : (
            <>
              <div className={s.list}>
                {list.map((order) => (
                  <OrderRow key={order.id} order={order} onOpen={() => setOpen(order)} />
                ))}
              </div>
              {orders.hasNextPage && (
                <Button
                  block
                  busy={orders.isFetchingNextPage}
                  onClick={() => void orders.fetchNextPage()}
                >
                  {t('orders.loadMore')}
                </Button>
              )}
            </>
          )}
        </>
      )}

      <BottomBar>
        <Button variant="primary" block onClick={() => navigate(`/s/${storeId}/counter`)}>
          {t('orders.newCounterSale')}
        </Button>
      </BottomBar>
      {open && <OrderSheet order={open} onClose={() => setOpen(null)} />}
    </Page>
  );
}

/** Today's total, opening the Analytics screen. */
function TodayLink({ onOpen }: { onOpen: () => void }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const { storeId } = useStore();
  const today = useAnalytics(storeId, 'today');
  return (
    <button type="button" className={s.todayLink} onClick={onOpen}>
      <span className={s.todayIcon}>
        <Icon name="chart" />
      </span>
      <span className={s.todayMain}>
        <span className={s.todayTitle}>{t('orders.seeAnalytics')}</span>
        <span className={s.muted}>{t('orders.seeAnalyticsHint')}</span>
      </span>
      <span className={s.todayTotal}>{today.data ? money(today.data.revenue, language) : '…'}</span>
      <Icon name="chevronRight" />
    </button>
  );
}

function StatusBadge({ order }: { order: Order }) {
  const { t } = useTranslation();
  if (order.status === 'cancelled') return <Badge tone="neutral">{t('orders.cancelled')}</Badge>;
  if (order.channel === 'telegram' && order.status === 'delivered') {
    return <Badge tone="neutral">{t('orders.statusDelivered')}</Badge>;
  }
  if (order.payment_status === 'paid') return <Badge tone="success">{t('orders.paid')}</Badge>;
  if (order.payment_status === 'refunded')
    return <Badge tone="neutral">{t('orders.refunded')}</Badge>;
  return <Badge tone="info">{t('orders.statusNew')}</Badge>;
}

function OrderRow({ order, onOpen }: { order: Order; onOpen: () => void }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const shop = order.channel === 'in_shop';
  const who = order.customer.name || (shop ? t('orders.walkIn') : '—');
  // What was bought comes first: the first item, then "+ 2 more".
  const [first, ...others] = order.items;
  const what = first
    ? [first.name ?? '—', variantLabel(first.color, first.size)].filter(Boolean).join(' · ') +
      (first.quantity > 1 ? ` × ${first.quantity}` : '')
    : `#${order.number}`;
  const time = new Date(order.created_at).toLocaleTimeString(
    language === 'am' ? 'am-ET' : 'en-GB',
    {
      timeZone: 'Africa/Addis_Ababa',
      hour: '2-digit',
      minute: '2-digit',
    },
  );
  return (
    <button type="button" className={s.row} onClick={onOpen}>
      <span className={s.rowIcon} aria-hidden="true">
        {shop ? '🏪' : <Icon name="send" size={20} />}
      </span>
      <span className={s.rowMain}>
        <span className={s.rowTitle}>
          {what}
          {others.length > 0 && (
            <span className={s.rowMore}> {t('orders.moreItems', { count: others.length })}</span>
          )}
        </span>
        <span className={s.rowSub}>
          #{order.number} · {who} · {shop ? t('orders.inShop') : t('orders.telegram')} · {time}
        </span>
      </span>
      <span className={s.rowEnd}>
        <span className={s.rowTotal}>{money(order.total, language)}</span>
        <StatusBadge order={order} />
      </span>
    </button>
  );
}

/** Everything about one order (tap on a row). */
function OrderSheet({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const shop = order.channel === 'in_shop';
  const discount = order.items.reduce((sum, item) => {
    const listed = item.list_price === null ? Number(item.price) : Number(item.list_price);
    return sum + Math.max(0, listed - Number(item.price)) * item.quantity;
  }, 0);
  return (
    <Sheet open onClose={onClose} label={t('orders.order', { number: order.number })}>
      <div className={s.orderHead}>
        <span className={s.orderNumber}>{t('orders.order', { number: order.number })}</span>
        <StatusBadge order={order} />
      </div>
      <div className={s.muted}>
        {orderTime(order.created_at, language)} ·{' '}
        {shop
          ? `🏪 ${t('orders.inShop')}`
          : order.fulfillment === 'delivery'
            ? t('orders.delivery')
            : t('orders.pickup')}
      </div>
      <ul className={s.items}>
        {order.items.map((item, index) => {
          const listed = item.list_price === null ? null : Number(item.list_price);
          const paid = Number(item.price);
          return (
            <li key={index} className={s.itemLine}>
              <span>
                {t('orders.item', {
                  name: item.name ?? '—',
                  variant: variantLabel(item.color, item.size) || '—',
                  quantity: item.quantity,
                })}
                {listed !== null && listed > paid && (
                  <span className={s.muted}>
                    {' '}
                    ({t('orders.listed', { price: money(listed, language) })}, −
                    {percentOff(listed, paid)}%)
                  </span>
                )}
              </span>
              <span>{money(paid * item.quantity, language)}</span>
            </li>
          );
        })}
      </ul>
      {discount > 0 && (
        <div className={s.sheetLine}>
          <span>{t('orders.discount')}</span>
          <span>−{money(discount, language)}</span>
        </div>
      )}
      <div className={`${s.sheetLine} ${s.sheetTotal}`}>
        <span>{t('counter.toPay')}</span>
        <span>{money(order.total, language)}</span>
      </div>
      <div className={s.sheetMeta}>
        {(order.customer.name || order.customer.phone) && (
          <p>{[order.customer.name, order.customer.phone].filter(Boolean).join(' · ')}</p>
        )}
        {order.delivery_address && <p>{order.delivery_address}</p>}
        {order.sold_by && <p>{t('orders.soldBy', { name: order.sold_by })}</p>}
        {order.payment_method && (
          <p>
            {t('orders.paidWith', { method: order.payment_method })}
            {order.payment_note ? ` (${order.payment_note})` : ''}
          </p>
        )}
        {order.note && (
          <p>
            {t('orders.note')}: {order.note}
          </p>
        )}
        {!shop && order.payment_status === 'unpaid' && <p>{t('orders.paymentInGroup')}</p>}
      </div>
    </Sheet>
  );
}
