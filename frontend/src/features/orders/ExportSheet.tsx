import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useExportOrders } from '@/api/queries';
import { useErrorText, useLanguage, useStore } from '@/app/hooks';
import { Button, Chips, Sheet } from '@/components/ui';
import { monthName, recentMonths } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import s from './orders.module.css';

/** Phase 15 (D72/D73): owners pick a month; the bot sends the Excel file. */
export function ExportSheet({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const errorText = useErrorText();
  const { storeId, store } = useStore();
  const exportOrders = useExportOrders(storeId);
  const months = recentMonths();
  const [month, setMonth] = useState(months[0] ?? '');

  const send = () =>
    exportOrders.mutate(month, {
      onSuccess: () => {
        haptic.success();
        toast.info(
          store.bot_username
            ? t('exportSheet.sent', { bot: store.bot_username })
            : t('exportSheet.sentNoBot'),
        );
        onClose();
      },
      onError: (error) => toast.error(errorText(error)),
    });

  return (
    <Sheet
      open
      onClose={onClose}
      label={t('exportSheet.title')}
      footer={
        <Button variant="primary" block icon="send" busy={exportOrders.isPending} onClick={send}>
          {t('exportSheet.send')}
        </Button>
      }
    >
      <h2 className={s.orderNumber}>{t('exportSheet.title')}</h2>
      <p className={s.muted}>{t('exportSheet.hint')}</p>
      <Chips
        label={t('exportSheet.month')}
        value={month}
        onChange={setMonth}
        options={months.map((value) => ({ value, label: monthName(value, language) }))}
      />
    </Sheet>
  );
}
