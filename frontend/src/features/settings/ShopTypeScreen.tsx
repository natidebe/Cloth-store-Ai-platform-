import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router';

import { useSaveSettings, useSettings } from '@/api/queries';
import type { OptionRenames, ShopType, StoreSettings } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useBackButton, useErrorText, useLanguage, useStore } from '@/app/hooks';
import { ShopTypeChoices } from '@/components/ShopTypeChoices';
import {
  BottomBar,
  Button,
  Card,
  Field,
  Notice,
  Page,
  PageHeader,
  SkeletonList,
  TextInput,
} from '@/components/ui';
import { shopWords } from '@/lib/shopWords';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import s from './settings.module.css';

type Option = 'option1' | 'option2';
type Renames = Record<Option, { en: string; am: string }>;

const OPTIONS: Option[] = ['option1', 'option2'];

/** Phase 13 (D59, D61): the kind of shop, and the owner's own words for the two options. */
export function ShopTypeScreen() {
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
        <SkeletonList rows={4} />
      </Page>
    );
  }
  return <ShopTypeForm settings={settings.data} />;
}

function renamesOf(labels: OptionRenames): Renames {
  return {
    option1: { en: labels?.option1?.en ?? '', am: labels?.option1?.am ?? '' },
    option2: { en: labels?.option2?.en ?? '', am: labels?.option2?.am ?? '' },
  };
}

function ShopTypeForm({ settings }: { settings: StoreSettings }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const language = useLanguage();
  const errorText = useErrorText();
  const { storeId, role } = useStore();
  const save = useSaveSettings(storeId);
  const [type, setType] = useState<ShopType>(settings.shop_type);
  const [renames, setRenames] = useState<Renames>(() => renamesOf(settings.option_labels));

  const preset = settings.shop_types.find((info) => info.type === type) ?? settings.shop_types[0];
  if (!preset) return null;
  // What the bot will say, with the renames (empty = the type's word).
  const option = (key: Option) => ({
    ...preset[key],
    en: renames[key].en.trim() || preset[key].en,
    am: renames[key].am.trim() || preset[key].am,
    plural: renames[key].en.trim() || preset[key].plural,
  });
  const words = shopWords(
    { shop_type: type, option1: option('option1'), option2: option('option2') },
    language,
  );

  const original = JSON.stringify([settings.shop_type, renamesOf(settings.option_labels)]);
  const dirty = JSON.stringify([type, renames]) !== original;

  const set = (key: Option, lang: 'en' | 'am', value: string) =>
    setRenames((current) => ({ ...current, [key]: { ...current[key], [lang]: value } }));

  const submit = () => {
    const option_labels: OptionRenames = {};
    for (const key of OPTIONS) {
      const en = renames[key].en.trim();
      const am = renames[key].am.trim();
      if (en || am) option_labels[key] = { ...(en ? { en } : {}), ...(am ? { am } : {}) };
    }
    save.mutate(
      { shop_type: type, option_labels: Object.keys(option_labels).length ? option_labels : null },
      {
        onSuccess: () => {
          haptic.success();
          toast.info(t('shopType.saved'));
          void navigate(`/s/${storeId}/settings`);
        },
        onError: (error) => toast.error(errorText(error)),
      },
    );
  };

  return (
    <Page>
      <PageHeader title={t('shopType.title')} role={role} />
      <p className={s.lead}>{t('shopType.subtitle')}</p>

      <Card label={t('shopType.kind')}>
        <ShopTypeChoices
          types={settings.shop_types}
          value={type}
          onChange={setType}
          language={language}
          label={t('shopType.kind')}
        />
        {preset.condition_and_warranty && (
          <p className={s.discountExample}>{t('shopType.extras')}</p>
        )}
      </Card>

      <Card label={t('shopType.words')}>
        <p className={s.emptyHint}>{t('shopType.wordsHint')}</p>
        {OPTIONS.map((key) => (
          <div key={key} className={s.renameGroup}>
            <p className={s.renameTitle}>
              {t(`shopType.${key}`)}: <strong>{option(key)[language]}</strong>
            </p>
            <div className={s.renameRow}>
              <Field label={t('shopType.english')}>
                {(id) => (
                  <TextInput
                    id={id}
                    maxLength={20}
                    placeholder={preset[key].en}
                    value={renames[key].en}
                    onChange={(event) => set(key, 'en', event.target.value)}
                  />
                )}
              </Field>
              <Field label={t('shopType.amharic')}>
                {(id) => (
                  <TextInput
                    id={id}
                    maxLength={20}
                    placeholder={preset[key].am}
                    value={renames[key].am}
                    onChange={(event) => set(key, 'am', event.target.value)}
                  />
                )}
              </Field>
            </div>
          </div>
        ))}
        <Notice tone="info" icon="bot">
          {t('shopType.preview', words)}
        </Notice>
      </Card>

      <BottomBar>
        <Button variant="primary" block busy={save.isPending} disabled={!dirty} onClick={submit}>
          {t('common.save')}
        </Button>
      </BottomBar>
    </Page>
  );
}
