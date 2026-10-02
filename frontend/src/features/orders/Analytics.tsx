import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAnalytics } from '@/api/queries';
import type { Analytics as Numbers, Period } from '@/api/types';
import { useLanguage, useStore } from '@/app/hooks';
import { Card, Segmented } from '@/components/ui';
import { dayLabel, money, percent } from '@/lib/format';

import s from './orders.module.css';

/** The store's performance, at the top of the Orders tab. */
export function Analytics() {
  const { t } = useTranslation();
  const { storeId } = useStore();
  const [period, setPeriod] = useState<Period>('today');
  const numbers = useAnalytics(storeId, period);

  return (
    <section aria-busy={numbers.isFetching}>
      <Segmented
        label={t('analytics.revenue')}
        value={period}
        onChange={setPeriod}
        options={[
          { value: 'today', label: t('analytics.today') },
          { value: '7d', label: t('analytics.week') },
          { value: '30d', label: t('analytics.month') },
        ]}
      />
      {numbers.data ? <Body data={numbers.data} /> : <Placeholder />}
    </section>
  );
}

function Body({ data }: { data: Numbers }) {
  const { t } = useTranslation();
  const language = useLanguage();
  return (
    <>
      <div className={s.stats}>
        <div className={`${s.stat} ${s.statWide}`}>
          <div className={s.statLabel}>{t('analytics.revenue')}</div>
          <div className={`${s.statValue} ${s.statBig}`}>{money(data.revenue, language)}</div>
        </div>
        <div className={s.stat}>
          <div className={s.statValue}>{data.orders_placed}</div>
          <div className={s.statLabel}>{t('analytics.orders')}</div>
          <div className={s.statSub}>
            {t('analytics.ordersPaid', { paid: data.orders_paid, placed: data.orders_placed })}
          </div>
        </div>
        <div className={s.stat}>
          <div className={s.statValue}>{percent(data.paid_rate)}</div>
          <div className={s.statLabel}>{t('analytics.paidRate')}</div>
          <div className={s.statSub}>
            {t('analytics.averageOrder')}: {money(data.average_order, language)}
          </div>
        </div>
      </div>

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
        <Mini value={data.unpaid_orders} label={t('analytics.unpaid')} />
        <Mini value={data.new_customers} label={t('analytics.newCustomers')} />
        <Mini
          value={`${data.delivery_orders} / ${data.pickup_orders}`}
          label={t('analytics.deliveryPickup')}
        />
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

/** Revenue per day as bars; labels thinned out for 30 days. */
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

function Placeholder() {
  return (
    <div className={s.stats} aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className={`${s.stat} ${i === 0 ? s.statWide : ''}`}
          style={{ height: i === 0 ? 72 : 84 }}
        />
      ))}
    </div>
  );
}
