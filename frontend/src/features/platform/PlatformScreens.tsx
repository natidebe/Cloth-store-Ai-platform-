import { useTranslation } from 'react-i18next';
import { Navigate, Outlet, useNavigate } from 'react-router';

import { usePlatformMe } from '@/api/platformQueries';
import type { StoreCard } from '@/api/types';
import { LoadFailed } from '@/app/AccessScreens';
import { Icon } from '@/components/Icon';
import {
  Badge,
  BottomBar,
  Button,
  Card,
  Notice,
  Page,
  PageHeader,
  SkeletonList,
} from '@/components/ui';
import { openTelegramLink } from '@/lib/telegram';

import { statusTone, usePlatform } from './hooks';
import s from './platform.module.css';

/** Everything under /app/platform: loads who I am in the platform bot first. */
export function PlatformShell() {
  const me = usePlatformMe();
  if (me.isPending) {
    return (
      <Page>
        <SkeletonList rows={2} />
      </Page>
    );
  }
  if (me.isError) return <LoadFailed error={me.error} onRetry={() => void me.refetch()} />;
  return <Outlet />;
}

/** /app/platform: sign up, wait for approval, or open your stores. */
export function PlatformHome() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const me = usePlatform();

  if (me.stores.length === 0) return <Navigate to="/platform/create" replace />;
  const [only] = me.stores;
  if (me.stores.length === 1 && only?.status === 'pending' && !me.is_platform_admin) {
    return <WaitingForApproval store={only} supportUrl={me.support_url} />;
  }

  return (
    <Page bar={false}>
      <PageHeader title={t('platform.myStores')} />
      {me.is_platform_admin && (
        <Button variant="soft" icon="store" block onClick={() => navigate('/platform/admin')}>
          {t('platform.adminStores')}
        </Button>
      )}
      <div style={{ height: 12 }} />
      {me.stores.map((store) => (
        <Card key={store.id}>
          <div className={s.storeCard}>
            <span className={s.avatar}>
              <Icon name="store" />
            </span>
            <span className={s.storeMain}>
              <span className={s.storeName} style={{ display: 'block' }}>
                {store.name}
              </span>
              <span className={s.storeBot}>@{store.bot_username}</span>
            </span>
            <Badge tone={statusTone(store.status)}>{t(`status.${store.status}`)}</Badge>
          </div>
          {store.status === 'suspended' && (
            <div style={{ marginTop: 12 }}>
              <Notice tone="danger">{t('platform.suspendedBody')}</Notice>
            </div>
          )}
          <div className={`${s.actions} ${s.actionsOne}`}>
            <Button
              icon="chart"
              onClick={() => openTelegramLink(`https://t.me/${store.bot_username}?start=dashboard`)}
            >
              {t('platform.openDashboard')}
            </Button>
          </div>
        </Card>
      ))}
      <Button variant="link" icon="plus" onClick={() => navigate('/platform/create')}>
        {t('platform.createAnother')}
      </Button>
    </Page>
  );
}

/** Design: "Waiting for approval". */
function WaitingForApproval({
  store,
  supportUrl,
}: {
  store: StoreCard;
  supportUrl: string | null;
}) {
  const { t } = useTranslation();
  const me = usePlatformMe();
  return (
    <Page>
      <div className={s.waitingHead}>
        <div className={s.clock}>
          <Icon name="clock" size={34} />
        </div>
        <h1 className={s.waitingTitle}>{t('platform.waitingTitle')}</h1>
        <p className={s.waitingBody}>{t('platform.waitingBody')}</p>
      </div>
      <Card>
        <div className={s.storeCard}>
          <span className={s.avatar}>
            <Icon name="store" />
          </span>
          <span className={s.storeMain}>
            <span className={s.storeName} style={{ display: 'block' }}>
              {store.name}
            </span>
            <span className={s.storeBot}>@{store.bot_username}</span>
          </span>
          <Badge tone="warn">{t('status.pending')}</Badge>
        </div>
      </Card>
      <Card>
        <ol className={s.progress}>
          <li>
            <span className={`${s.mark} ${s.markDone}`}>
              <Icon name="check" size={15} />
            </span>
            {t('platform.submitted')}
          </li>
          <li>
            <span className={`${s.mark} ${s.markNow}`} />
            {t('platform.underReview')}
          </li>
          <li className={s.progressTodo}>
            <span className={`${s.mark} ${s.markTodo}`} />
            {t('platform.goesLive')}
          </li>
        </ol>
      </Card>
      {supportUrl && (
        <div className={s.center}>
          <Button variant="soft" onClick={() => openTelegramLink(supportUrl)}>
            {t('platform.contactSupport')}
          </Button>
        </div>
      )}
      <BottomBar>
        <Button variant="primary" block busy={me.isFetching} onClick={() => void me.refetch()}>
          {t('platform.checkStatus')}
        </Button>
      </BottomBar>
    </Page>
  );
}
