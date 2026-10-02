import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAnalytics } from '@/api/queries';
import type { Analytics as Numbers, Period } from '@/api/types';
import { LoadFailed } from '@/app/AccessScreens';
import { useBackButton, useLanguage, useStore } from '@/app/hooks';
import { Icon } from '@/components/Icon';
import { Card, Page, PageHeader, Segmented, SkeletonList } from '@/components/ui';
import { dayLabel, money, percent } from '@/lib/format';

import s from './orders.module.css';

/** Design: "Analytics channels and discounts", plus the day chart and top products. */
export function AnalyticsScreen() {
  const { t } = useTranslation();
  const { storeId, role } = useStore();
  const [period, setPeriod] = useState<Period>('today');
  const numbers = useAnalytics(storeId, period);
  useBackButton(`/s/${storeId}/orders`);

  if (numbers.isError && !numbers.data) {
    return <LoadFailed error={numbers.error} onRetry={() => void numbers.refetch()} />;
  }

  return (
    <Page bar={false}>
      <PageHeader title={t('analytics.title')} role={role} />
      <Segmented
        label={t('analytics.title')}
        value={period}
        onChange={setPeriod}
        options={[
          { value: 'today', label: t('analytics.today') },
          { value: 'week', label: t('analytics.thisWeek') },
          { value: 'month', label: t('analytics.thisMonth') },
        ]}
      />
      <section aria-busy={numbers.isFetching}>
        {numbers.data ? <Body data={numbers.data} /> : <SkeletonList rows={3} />}
      </section>
    </Page>
  );
}

function Body({ data }: { data: Numbers }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const shop = Number(data.in_shop_revenue);
  const telegram = Math.max(0, Number(data.revenue) - shop);
  const total = telegram + shop;
  const sellers = data.sellers.filter((seller) => Number(seller.discount) > 0);
  return (
    <>
      <Card label={t('analytics.sales')}>
        <div className={s.bigNumber}>{money(data.revenue, language)}</div>
        <div className={s.muted}>{t('analytics.ordersCount', { count: data.orders_placed })}</div>
        <div className={s.splitBar} aria-hidden="true">
          {total > 0 && (
            <>
              <span className={s.splitTelegram} style={{ flexGrow: telegram }} />
              <span className={s.splitShop} style={{ flexGrow: shop }} />
            </>
          )}
        </div>
        <div className={s.splitCols}>
          <div>
            <span className={s.legend}>
              <span className={`${s.legendDot} ${s.splitTelegram}`} />
              {t('orders.telegram')}
            </span>
            <div className={s.splitAmount}>{money(telegram, language)}</div>
            <div className={s.muted}>
              {t('analytics.ordersCount', { count: data.telegram_orders })}
            </div>
          </div>
          <div>
            <span className={s.legend}>
              <span className={`${s.legendDot} ${s.splitShop}`} />
              🏪 {t('orders.inShop')}
            </span>
            <div className={s.splitAmount}>{money(shop, language)}</div>
            <div className={s.muted}>
              {t('analytics.ordersCount', { count: data.in_shop_sales })}
            </div>
          </div>
        </div>
      </Card>

      <Card label={t('analytics.discountsGiven')}>
        <div className={s.bigNumber}>{money(data.discount_total, language)}</div>
        <div className={s.muted}>
          {t('analytics.belowListed', { count: data.discounted_items })}
        </div>
        {sellers.length > 0 ? (
          <>
            <hr className={s.divider} />
            <p className={s.muted} style={{ fontWeight: 600, margin: '0 0 4px' }}>
              {t('analytics.byStaff')}
            </p>
            {sellers.map((seller) => (
              <div key={String(seller.telegram_id)} className={s.staffRow}>
                <span className={s.staffAvatar}>
                  <Icon name="bot" size={16} />
                </span>
                <span className={s.staffName}>{seller.name ?? '—'}</span>
                <strong>{money(seller.discount, language)}</strong>
              </div>
            ))}
          </>
        ) : (
          Number(data.discount_total) === 0 && (
            <p className={s.muted} style={{ margin: '8px 0 0' }}>
              {t('analytics.noDiscounts')}
            </p>
          )
        )}
      </Card>

      {data.per_day.length > 1 && (
        <Card label={t('analytics.salesPerDay')}>
          <SalesChart days={data.per_day} />
        </Card>
      )}

      <Card label={t('analytics.topProducts')}>
        {data.top_products.length === 0 ? (
          <p className={s.muted} style={{ margin: 0 }}>
            {t('analytics.noSales')}
          </p>
        ) : (
          data.top_products.map((product, index) => (
            <div key={product.product_id} className={s.top}>
              <span className={s.rank}>{index + 1}</span>
              <span className={s.topMain}>
                <span className={s.topName} style={{ display: 'block' }}>
                  {product.name}
                </span>
                <span className={s.muted}>{t('analytics.sold', { count: product.quantity })}</span>
              </span>
              <span className={s.muted}>{money(product.revenue, language)}</span>
            </div>
          ))
        )}
      </Card>

      <div className={s.minis}>
        <Mini value={percent(data.paid_rate)} label={t('analytics.paidRate')} />
        <Mini value={money(data.average_order, language)} label={t('analytics.averageOrder')} />
        <Mini value={data.new_customers} label={t('analytics.newCustomers')} />
      </div>
    </>
  );
}

function Mini({ value, label }: { value: number | string; label: string }) {
  return (
    <div className={s.mini}>
      <div className={s.miniValue}>{value}</div>
      <div className={s.muted}>{label}</div>
    </div>
  );
}

/** Revenue per day as bars; labels thinned out for long periods. */
function SalesChart({ days }: { days: Numbers['per_day'] }) {
  const language = useLanguage();
  const max = Math.max(...days.map((d) => Number(d.revenue)), 1);
  const every = days.length > 10 ? Math.ceil(days.length / 6) : 1;
  return (
    <figure
      style={{ margin: 0 }}
      aria-label={days.map((d) => `${d.day}: ${money(d.revenue, language)}`).join(', ')}
    >
      <div className={s.chart}>
        {days.map((day) => {
          const value = Number(day.revenue);
          return (
            <div
              key={day.day}
              className={s.barWrap}
              title={`${dayLabel(day.day, language)}: ${money(day.revenue, language)}`}
            >
              <div
                className={`${s.bar} ${value === 0 ? s.barZero : ''}`}
                style={{ height: `${(value / max) * 100}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className={s.chartLabels} aria-hidden="true">
        {days.map((day, index) => (
          <span key={day.day} className={s.chartLabel}>
            {index % every === 0 ? dayLabel(day.day, language) : ''}
          </span>
        ))}
      </div>
    </figure>
  );
}
