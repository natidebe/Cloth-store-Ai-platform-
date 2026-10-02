import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useLanguage, useStore } from '@/app/hooks';
import { StoreTabs } from '@/app/StoreShell';
import { Icon } from '@/components/Icon';
import { Card, Notice, Page, PageHeader, Row, Segmented } from '@/components/ui';
import { usePreferences, type Language } from '@/state/preferences';

/** Design: "Settings" (owner) / "Staff settings locked". */
export function SettingsScreen() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { storeId, role, isOwner, store } = useStore();
  const language = useLanguage();
  const setLanguage = usePreferences((state) => state.setLanguage);
  const base = `/s/${storeId}/settings`;

  const connected =
    store.staff_group_linked && store.channel_linked
      ? t('settings.connectedBoth')
      : store.staff_group_linked
        ? t('settings.connectedGroup')
        : store.channel_linked
          ? t('settings.connectedChannel')
          : t('settings.notConnected');
  const end = isOwner ? <Icon name="chevronRight" /> : <Icon name="lock" size={18} />;
  const go = (path: string) => (isOwner ? () => navigate(`${base}/${path}`) : undefined);

  return (
    <Page bar={false}>
      <PageHeader title={t('settings.title')} role={role} />
      <StoreTabs />
      {!isOwner && (
        <Notice tone="warn" icon="lock">
          {t('settings.ownerOnly')}
        </Notice>
      )}

      <Card flush>
        <Row
          icon="card"
          muted={!isOwner}
          title={t('settings.storeProfile')}
          subtitle={t('settings.storeProfileHint')}
          end={end}
          onClick={go('profile')}
        />
        <Row
          icon="link"
          muted={!isOwner}
          title={t('settings.connect')}
          subtitle={connected}
          end={end}
          onClick={go('connect')}
        />
        <Row
          icon="bot"
          muted={!isOwner}
          title={t('settings.changeBot')}
          subtitle={store.bot_username ? `@${store.bot_username}` : '—'}
          end={end}
          onClick={go('bot')}
        />
        <Row
          icon="card"
          muted={!isOwner}
          title={t('settings.staffDiscount')}
          subtitle={
            Number(store.staff_discount_percent) > 0
              ? t('settings.staffDiscountRow', { percent: Number(store.staff_discount_percent) })
              : t('settings.staffDiscountNone')
          }
          end={end}
          onClick={go('discount')}
        />
      </Card>

      <Card label={t('settings.language')}>
        <Segmented<Language>
          label={t('settings.language')}
          value={language}
          onChange={setLanguage}
          options={[
            { value: 'am', label: 'አማርኛ' },
            { value: 'en', label: 'English' },
          ]}
        />
      </Card>
    </Page>
  );
}
