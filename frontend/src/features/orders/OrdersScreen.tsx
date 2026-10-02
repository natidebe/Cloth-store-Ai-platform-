import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useOrders } from '@/api/queries';
import type { Order, OrderFilter } from '@/api/types';
import { LoadFailed } from '@/app/AccessScreens';
import { useLanguage, useStore } from '@/app/hooks';
import { StoreTabs } from '@/app/StoreShell';
import { Badge, Button, Centered, Chips, Page, PageHeader, SkeletonList } from '@/components/ui';
import { money, orderTime, variantLabel } from '@/lib/format';
import { openTelegramLink } from '@/lib/telegram';

import { Analytics } from './Analytics';
import s from './orders.module.css';

/** The Orders tab: how the store is doing, then the orders (view only, D46). */
export function OrdersScreen() {
  const { t } = useTranslation();
  const { storeId, role, store } = useStore();
  const [filter, setFilter] = useState<OrderFilter>('all');
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
    <Page bar={false}>
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
          <Analytics />
          <h2 className={s.sectionTitle}>{t('orders.title')}</h2>
          <Chips
            label={t('orders.title')}
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: t('orders.filterAll') },
              { value: 'unpaid', label: t('orders.filterUnpaid') },
              { value: 'paid', label: t('orders.filterPaid') },
            ]}
          />
          {orders.isPending ? (
            <SkeletonList rows={3} />
          ) : list.length === 0 ? (
            <p className={s.muted} style={{ textAlign: 'center' }}>
              {t('analytics.noSales')}
            </p>
          ) : (
            <>
              {filter === 'unpaid' && <p className={s.muted}>{t('orders.paymentInGroup')}</p>}
              {list.map((order) => (
                <OrderCard key={order.id} order={order} />
              ))}
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
    </Page>
  );
}

function OrderCard({ order }: { order: Order }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const payment =
    order.status === 'cancelled' ? (
      <Badge tone="neutral">{t('orders.cancelled')}</Badge>
    ) : order.payment_status === 'paid' ? (
      <Badge tone="success">{t('orders.paid')}</Badge>
    ) : order.payment_status === 'refunded' ? (
      <Badge tone="neutral">{t('orders.refunded')}</Badge>
    ) : (
      <Badge tone="warn">{t('orders.unpaid')}</Badge>
    );
  return (
    <article className={s.order}>
      <div className={s.orderHead}>
        <span className={s.orderNumber}>{t('orders.order', { number: order.number })}</span>
        {payment}
      </div>
      <div className={s.muted}>
        {orderTime(order.created_at, language)} ·{' '}
        {order.fulfillment === 'delivery' ? t('orders.delivery') : t('orders.pickup')}
      </div>
      <ul className={s.items}>
        {order.items.map((item, index) => (
          <li key={index}>
            {t('orders.item', {
              name: item.name ?? '—',
              variant: variantLabel(item.color, item.size) || '—',
              quantity: item.quantity,
            })}
          </li>
        ))}
      </ul>
      <div className={s.orderFoot}>
        <span className={s.muted}>
          {[order.customer.name, order.customer.phone].filter(Boolean).join(' · ')}
          {order.delivery_address && (
            <>
              <br />
              {order.delivery_address}
            </>
          )}
        </span>
        <span className={s.total}>{money(order.total, language)}</span>
      </div>
    </article>
  );
}
