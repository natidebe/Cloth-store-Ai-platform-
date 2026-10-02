import { useTranslation } from 'react-i18next';
import { NavLink, Outlet } from 'react-router';

import { useMe } from '@/api/queries';
import { segmentClasses } from '@/components/classes';
import { Icon } from '@/components/Icon';
import { Page, PageHeader, SkeletonList } from '@/components/ui';

import { LoadFailed } from './AccessScreens';
import { useStore, useStoreId } from './hooks';

/**
 * Everything under /app/s/<store>/: checks who you are first (GET /me), so
 * every screen inside can rely on useStore(). 403: not in the staff group.
 */
export function StoreShell() {
  const storeId = useStoreId();
  const me = useMe(storeId);
  const { t } = useTranslation();

  if (me.isPending) {
    return (
      <Page>
        <PageHeader title={t('products.title')} />
        <SkeletonList />
      </Page>
    );
  }
  if (me.isError) return <LoadFailed error={me.error} onRetry={() => void me.refetch()} />;
  return <Outlet />;
}

/** Products · Orders · Settings (design: the segmented control under the title). */
export function StoreTabs() {
  const { t } = useTranslation();
  const { storeId, isOwner } = useStore();
  const base = `/s/${storeId}`;
  const className = ({ isActive }: { isActive: boolean }) =>
    [segmentClasses.item, isActive && segmentClasses.active].filter(Boolean).join(' ');
  return (
    <nav className={segmentClasses.segment} aria-label={t('tabs.products')}>
      <NavLink to={`${base}/products`} className={className}>
        {t('tabs.products')}
      </NavLink>
      <NavLink to={`${base}/orders`} className={className}>
        {t('tabs.orders')}
      </NavLink>
      <NavLink to={`${base}/settings`} className={className}>
        {t('tabs.settings')}
        {!isOwner && <Icon name="lock" size={14} />}
      </NavLink>
    </nav>
  );
}
