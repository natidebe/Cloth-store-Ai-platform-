import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router';

import { useSaveSettings, useSettings } from '@/api/queries';
import { ErrorScreen } from '@/app/AccessScreens';
import { useBackButton, useErrorText, useLanguage, useStore } from '@/app/hooks';
import {
  BottomBar,
  Button,
  Card,
  Notice,
  Page,
  PageHeader,
  SkeletonList,
  Stepper,
} from '@/components/ui';
import { money } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { lowestPrice } from '@/state/counterDraft';
import { toast } from '@/state/toasts';

import s from './settings.module.css';

const EXAMPLE_PRICE = 1000;

/** Design: "Staff discount limit" (D53). Owner only. */
export function StaffDiscountScreen() {
  const { storeId, isOwner } = useStore();
  const settings = useSettings(storeId, isOwner);
  useBackButton(`/s/${storeId}/settings`);

  if (!isOwner) return <Navigate to={`/s/${storeId}/settings`} replace />;
  if (settings.isError) {
    return <ErrorScreen error={settings.error} onRetry={() => void settings.refetch()} />;
  }
  if (!settings.data) {
    return (
      <Page>
        <SkeletonList rows={3} />
      </Page>
    );
  }
  return <DiscountForm saved={Number(settings.data.staff_discount_percent)} />;
}

function DiscountForm({ saved }: { saved: number }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const language = useLanguage();
  const errorText = useErrorText();
  const { storeId, role } = useStore();
  const save = useSaveSettings(storeId);
  const [percent, setPercent] = useState(saved);

  const allowed = percent > 0;
  const choose = (value: number) => {
    haptic.select();
    setPercent(value);
  };
  const submit = () =>
    save.mutate(
      { staff_discount_percent: String(percent) },
      {
        onSuccess: () => {
          toast.info(t('common.saved'));
          void navigate(`/s/${storeId}/settings`);
        },
        onError: (error) => toast.error(errorText(error)),
      },
    );

  return (
    <Page>
      <PageHeader title={t('discountLimit.title')} role={role} />
      <p className={s.lead}>{t('discountLimit.subtitle')}</p>

      <Card flush>
        <div role="radiogroup" aria-label={t('discountLimit.title')}>
          <Choice
            on={!allowed}
            title={t('discountLimit.none')}
            hint={t('discountLimit.noneHint')}
            onPick={() => choose(0)}
          />
          <Choice
            on={allowed}
            title={t('discountLimit.upTo')}
            hint={t('discountLimit.upToHint')}
            onPick={() => choose(allowed ? percent : 10)}
          />
        </div>
      </Card>

      {allowed && (
        <Card label={t('discountLimit.maximum')}>
          <div className={s.discountRow}>
            <span className={s.discountValue}>{percent}%</span>
            <Stepper
              label={t('discountLimit.maximum')}
              value={percent}
              min={1}
              max={100}
              onChange={setPercent}
            />
          </div>
          <p className={s.discountExample}>
            {t('discountLimit.example')}:{' '}
            {t('discountLimit.exampleLine', {
              listed: money(EXAMPLE_PRICE, language),
              lowest: money(lowestPrice(EXAMPLE_PRICE, percent), language),
            })}
          </p>
        </Card>
      )}

      <Notice tone="info" icon="lock">
        {t('discountLimit.ownersAny')}
      </Notice>

      <BottomBar>
        <Button
          variant="primary"
          block
          busy={save.isPending}
          disabled={percent === saved}
          onClick={submit}
        >
          {t('common.save')}
        </Button>
      </BottomBar>
    </Page>
  );
}

function Choice({
  on,
  title,
  hint,
  onPick,
}: {
  on: boolean;
  title: string;
  hint: string;
  onPick: () => void;
}) {
  return (
    <button type="button" role="radio" aria-checked={on} className={s.choice} onClick={onPick}>
      <span className={`${s.radio} ${on ? s.radioOn : ''}`} aria-hidden="true" />
      <span className={s.choiceMain}>
        <span className={s.choiceTitle}>{title}</span>
        <span className={s.choiceHint}>{hint}</span>
      </span>
    </button>
  );
}
