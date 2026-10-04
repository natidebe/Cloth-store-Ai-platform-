import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAdminStores, useStoreAdmin } from '@/api/platformQueries';
import type { AdminStore, PaymentInput, StoreStatus } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useErrorText, useLanguage } from '@/app/hooks';
import { Icon } from '@/components/Icon';
import {
  Badge,
  Button,
  Card,
  Centered,
  Chips,
  Field,
  Page,
  PageHeader,
  Sheet,
  SkeletonList,
  TextInput,
} from '@/components/ui';
import { GRACE_DAYS, PLAN_PRICES, nextEnd, planDay, planState } from '@/lib/plan';
import { confirmAction, haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import { statusTone, useBackToPlatform, usePlatform } from './hooks';
import s from './platform.module.css';

type Filter = 'all' | 'ending' | StoreStatus;
type PaidPlan = PaymentInput['plan'];
const METHODS = ['Telebirr', 'CBE', 'Cash', 'other'] as const;
type Method = (typeof METHODS)[number];

/** Ending soonest first; shops without an end date (waiting for approval) last. */
function byEnd(a: AdminStore, b: AdminStore): number {
  if (!a.plan_ends_at) return b.plan_ends_at ? 1 : 0;
  if (!b.plan_ends_at) return -1;
  return a.plan_ends_at.localeCompare(b.plan_ends_at);
}

/** 7 days or fewer left, ended, or paused for not paying. */
function endingSoon(store: AdminStore): boolean {
  const state = planState(store.plan_ends_at, store.suspended_reason);
  return store.status !== 'pending' && state.kind !== 'ok' && state.kind !== 'notStarted';
}

/**
 * Design: "Platform admin stores" + "Platform admin plan and suspend".
 * Phase 14: each shop's plan and days left, ending soonest first, and
 * Record payment (3 months more).
 */
export function AdminStoresScreen() {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const language = useLanguage();
  const isAdmin = usePlatform().is_platform_admin;
  const stores = useAdminStores(isAdmin);
  const admin = useStoreAdmin();
  const [filter, setFilter] = useState<Filter>('all');
  const [payFor, setPayFor] = useState<AdminStore | null>(null);
  useBackToPlatform();

  if (!isAdmin)
    return (
      <Page bar={false}>
        <Centered icon="lock" title={t('access.adminsOnly')} />
      </Page>
    );
  if (stores.isError)
    return <ErrorScreen error={stores.error} onRetry={() => void stores.refetch()} />;

  const done = (message?: string) => () => {
    haptic.success();
    if (message) toast.info(message);
  };
  const failed = (error: unknown) => toast.error(errorText(error));
  const suspend = async (store: AdminStore) => {
    if (!(await confirmAction(t('admin.suspendConfirm', { name: store.name })))) return;
    admin.suspend.mutate(store.id, { onSuccess: done(), onError: failed });
  };
  const shown = [...(stores.data ?? [])]
    .filter((store) =>
      filter === 'all' ? true : filter === 'ending' ? endingSoon(store) : store.status === filter,
    )
    .sort(byEnd);

  return (
    <Page bar={false}>
      <PageHeader title={t('admin.title')} />
      <Chips<Filter>
        label={t('admin.title')}
        value={filter}
        onChange={setFilter}
        options={[
          { value: 'all', label: t('admin.all') },
          { value: 'ending', label: t('admin.filterEnding') },
          { value: 'pending', label: t('status.pending') },
          { value: 'active', label: t('status.active') },
          { value: 'suspended', label: t('status.suspended') },
        ]}
      />
      {stores.isPending ? (
        <SkeletonList rows={4} />
      ) : shown.length === 0 ? (
        <p style={{ textAlign: 'center', color: 'var(--text-muted)' }}>{t('admin.empty')}</p>
      ) : (
        shown.map((store) => (
          <Card key={store.id}>
            <div className={s.storeCard}>
              <span className={s.avatar}>
                <Icon name="store" />
              </span>
              <span className={s.storeMain}>
                <span className={s.storeName} style={{ display: 'block' }}>
                  {store.name}
                </span>
                <span className={s.storeBot}>
                  @{store.telegram_bot_username ?? '—'} ·{' '}
                  {t('admin.orders', { count: store.orders })}
                </span>
                <PlanLine store={store} />
              </span>
              <Badge tone={statusTone(store.status)}>{t(`status.${store.status}`)}</Badge>
            </div>
            {store.status === 'pending' && (
              <div className={s.actions}>
                <Button
                  variant="primary"
                  icon="check"
                  busy={admin.approve.isPending && admin.approve.variables === store.id}
                  onClick={() =>
                    admin.approve.mutate(store.id, { onSuccess: done(), onError: failed })
                  }
                >
                  {t('admin.approve')}
                </Button>
                <Button variant="dangerOutline" onClick={() => void suspend(store)}>
                  {t('admin.suspend')}
                </Button>
              </div>
            )}
            {store.status === 'active' && (
              <div className={s.actions}>
                <Button variant="primary" onClick={() => setPayFor(store)}>
                  {t('admin.recordPayment')}
                </Button>
                <Button variant="dangerOutline" onClick={() => void suspend(store)}>
                  {t('admin.suspend')}
                </Button>
              </div>
            )}
            {store.status === 'suspended' && (
              <div className={s.actions}>
                <Button
                  variant={store.suspended_reason === 'unpaid' ? 'primary' : 'outline'}
                  onClick={() => setPayFor(store)}
                >
                  {t('admin.recordPayment')}
                </Button>
                <Button
                  variant={store.suspended_reason === 'unpaid' ? 'outline' : 'primary'}
                  busy={admin.approve.isPending && admin.approve.variables === store.id}
                  onClick={() =>
                    admin.approve.mutate(store.id, { onSuccess: done(), onError: failed })
                  }
                >
                  {t('admin.reactivate')}
                </Button>
              </div>
            )}
          </Card>
        ))
      )}

      {payFor && (
        <PaymentSheet
          store={payFor}
          busy={admin.recordPayment.isPending}
          onClose={() => setPayFor(null)}
          onSave={(payment) =>
            admin.recordPayment.mutate(
              { storeId: payFor.id, payment },
              {
                onSuccess: (result) => {
                  done()();
                  toast.info(
                    t('admin.paymentSaved', {
                      plan: t(`plans.${result.plan}`),
                      date: planDay(result.period_end, language),
                    }),
                  );
                  setPayFor(null);
                },
                onError: failed,
              },
            )
          }
        />
      )}
    </Page>
  );
}

/** "Basic · until Oct 11 · 7 days left", in color when it needs attention. */
function PlanLine({ store }: { store: AdminStore }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const state = planState(store.plan_ends_at, store.suspended_reason);
  const plan = t(`plans.${store.plan === 'basic' || store.plan === 'pro' ? store.plan : 'free'}`);
  const ends = store.plan_ends_at;
  let text: string;
  let tone = '';
  if (state.kind === 'notStarted' || !ends) {
    if (state.kind === 'paused') {
      text = t('admin.pausedUnpaid', { plan });
      tone = s.planDanger ?? '';
    } else text = t('admin.notStarted');
  } else if (state.kind === 'ok' || state.kind === 'soon') {
    text = t('admin.endsIn', { plan, count: state.days, date: planDay(ends, language) });
    if (state.kind === 'soon') tone = s.planWarn ?? '';
  } else if (state.kind === 'today') {
    text = t('admin.endsToday', { plan });
    tone = s.planWarn ?? '';
  } else if (state.kind === 'grace') {
    text = t('admin.inGrace', {
      plan,
      date: planDay(ends, language),
      pause: planDay(ends, language, GRACE_DAYS),
    });
    tone = s.planDanger ?? '';
  } else {
    text = t('admin.pausedUnpaid', { plan });
    tone = s.planDanger ?? '';
  }
  return <span className={`${s.planLine} ${tone}`}>{text}</span>;
}

/** Phase 14 (D65): a payment received by Telebirr, bank or cash: 3 months more. */
function PaymentSheet({
  store,
  busy,
  onClose,
  onSave,
}: {
  store: AdminStore;
  busy: boolean;
  onClose: () => void;
  onSave: (payment: PaymentInput) => void;
}) {
  const { t } = useTranslation();
  const language = useLanguage();
  const [plan, setPlan] = useState<PaidPlan>(store.plan === 'pro' ? 'pro' : 'basic');
  const [amount, setAmount] = useState(String(PLAN_PRICES[plan]));
  const [method, setMethod] = useState<Method>('Telebirr');
  const [otherMethod, setOtherMethod] = useState('');
  const [reference, setReference] = useState('');
  const value = Number(amount);
  const valid = amount.trim() !== '' && Number.isFinite(value) && value >= 0;

  const choosePlan = (next: PaidPlan) => {
    // The amount follows the plan unless it was typed by hand.
    if (Number(amount) === PLAN_PRICES[plan]) setAmount(String(PLAN_PRICES[next]));
    setPlan(next);
  };
  const save = () =>
    onSave({
      plan,
      amount: value,
      method: method === 'other' ? otherMethod.trim() || null : method,
      reference: reference.trim() || null,
    });

  return (
    <Sheet
      open
      onClose={onClose}
      label={t('admin.paymentTitle')}
      footer={
        <Button variant="primary" block busy={busy} disabled={!valid} onClick={save}>
          {t('admin.recordPayment')}
        </Button>
      }
    >
      <div className={s.storeCard} style={{ marginBottom: 12 }}>
        <span className={s.avatar}>
          <Icon name="store" />
        </span>
        <span className={s.storeMain}>
          <span className={s.storeName} style={{ display: 'block' }}>
            {store.name}
          </span>
          <span className={s.storeBot}>
            {t('admin.newEnd', { date: planDay(nextEnd(store.plan_ends_at), language) })}
          </span>
        </span>
      </div>
      <p className={s.sheetLabel}>{t('admin.paymentPlan')}</p>
      <div role="radiogroup" aria-label={t('admin.paymentPlan')}>
        {(['basic', 'pro'] as const).map((option) => (
          <label key={option} className={s.planOption}>
            <input
              type="radio"
              name="plan"
              value={option}
              checked={plan === option}
              onChange={() => choosePlan(option)}
            />
            {t(`plans.${option}`)} · {PLAN_PRICES[option].toLocaleString('en-US')} ETB
          </label>
        ))}
      </div>
      <Field label={t('admin.amount')}>
        {(id) => (
          <TextInput
            id={id}
            inputMode="decimal"
            value={amount}
            invalid={!valid}
            onChange={(event) => setAmount(event.target.value.replace(/[^\d.]/g, ''))}
          />
        )}
      </Field>
      <p className={s.sheetLabel}>{t('admin.paidWith')}</p>
      <Chips<Method>
        label={t('admin.paidWith')}
        value={method}
        onChange={setMethod}
        options={METHODS.map((m) => ({ value: m, label: m === 'other' ? t('admin.other') : m }))}
      />
      {method === 'other' && (
        <Field label={t('admin.otherMethod')}>
          {(id) => (
            <TextInput
              id={id}
              maxLength={60}
              value={otherMethod}
              onChange={(event) => setOtherMethod(event.target.value)}
            />
          )}
        </Field>
      )}
      <Field label={t('admin.reference')} hint={t('admin.referenceHint')}>
        {(id) => (
          <TextInput
            id={id}
            maxLength={120}
            value={reference}
            onChange={(event) => setReference(event.target.value)}
          />
        )}
      </Field>
    </Sheet>
  );
}
