import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAdminStores, useStoreAdmin } from '@/api/platformQueries';
import type { AdminStore, Plan, StoreStatus } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useErrorText } from '@/app/hooks';
import { Icon } from '@/components/Icon';
import {
  Badge,
  Button,
  Card,
  Centered,
  Chips,
  Page,
  PageHeader,
  Sheet,
  SkeletonList,
} from '@/components/ui';
import { confirmAction, haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import { statusTone, useBackToPlatform, usePlatform } from './hooks';
import s from './platform.module.css';

const PLANS: Plan[] = ['free', 'basic', 'pro'];
type Filter = 'all' | StoreStatus;

/** Design: "Platform admin stores" + "Platform admin plan and suspend". */
export function AdminStoresScreen() {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const isAdmin = usePlatform().is_platform_admin;
  const stores = useAdminStores(isAdmin);
  const admin = useStoreAdmin();
  const [filter, setFilter] = useState<Filter>('all');
  const [planFor, setPlanFor] = useState<AdminStore | null>(null);
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
    setPlanFor(null);
  };
  const shown = (stores.data ?? []).filter((store) => filter === 'all' || store.status === filter);

  return (
    <Page bar={false}>
      <PageHeader title={t('admin.title')} />
      <Chips<Filter>
        label={t('admin.title')}
        value={filter}
        onChange={setFilter}
        options={[
          { value: 'all', label: t('admin.all') },
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
                <Button onClick={() => setPlanFor(store)}>
                  {t('admin.plan', {
                    plan: t(
                      `plans.${(PLANS.includes(store.plan as Plan) ? store.plan : 'free') as Plan}`,
                    ),
                  })}
                </Button>
                <Button variant="dangerOutline" onClick={() => void suspend(store)}>
                  {t('admin.suspend')}
                </Button>
              </div>
            )}
            {store.status === 'suspended' && (
              <div className={`${s.actions} ${s.actionsOne}`}>
                <Button
                  variant="primary"
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

      {planFor && (
        <PlanSheet
          store={planFor}
          busy={admin.setPlan.isPending}
          onClose={() => setPlanFor(null)}
          onSuspend={() => void suspend(planFor)}
          onSave={(plan) =>
            admin.setPlan.mutate(
              { storeId: planFor.id, plan },
              {
                onSuccess: () => {
                  done(t('common.saved'))();
                  setPlanFor(null);
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

function PlanSheet({
  store,
  busy,
  onClose,
  onSave,
  onSuspend,
}: {
  store: AdminStore;
  busy: boolean;
  onClose: () => void;
  onSave: (plan: Plan) => void;
  onSuspend: () => void;
}) {
  const { t } = useTranslation();
  const [plan, setPlan] = useState<Plan>(
    PLANS.includes(store.plan as Plan) ? (store.plan as Plan) : 'free',
  );
  return (
    <Sheet
      open
      onClose={onClose}
      label={t('admin.planTitle')}
      footer={
        <Button variant="primary" block busy={busy} onClick={() => onSave(plan)}>
          {t('admin.savePlan')}
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
          <span className={s.storeBot}>@{store.telegram_bot_username ?? '—'}</span>
        </span>
      </div>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', fontWeight: 600, margin: '0 0 4px' }}>
        {t('admin.planTitle')}
      </p>
      <div role="radiogroup" aria-label={t('admin.planTitle')}>
        {PLANS.map((option) => (
          <label key={option} className={s.planOption}>
            <input
              type="radio"
              name="plan"
              value={option}
              checked={plan === option}
              onChange={() => setPlan(option)}
            />
            {t(`plans.${option}`)}
          </label>
        ))}
      </div>
      <div style={{ marginTop: 14 }}>
        <Button variant="dangerOutline" block onClick={onSuspend}>
          {t('admin.suspend')}
        </Button>
      </div>
    </Sheet>
  );
}
