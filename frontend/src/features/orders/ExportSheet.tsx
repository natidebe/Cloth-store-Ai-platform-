import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useExportOrders } from '@/api/queries';
import { useErrorText, useLanguage, useStore } from '@/app/hooks';
import type { ExportCalendar } from '@/api/types';
import { Button, Chips, Segmented, Sheet } from '@/components/ui';
import { ethiopianMonthName, recentEthiopianMonths } from '@/lib/ethiopian';
import { monthName, recentMonths } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import s from './orders.module.css';

/** Phase 15 (D72/D73): owners pick a month, GC or ዓ.ም. (D79); the bot sends the Excel file. */
export function ExportSheet({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const errorText = useErrorText();
  const { storeId, store } = useStore();
  const exportOrders = useExportOrders(storeId);
  // Amharic users get Ethiopian months first; the file has both calendars anyway.
  const [calendar, setCalendar] = useState<ExportCalendar>(
    language === 'am' ? 'ethiopian' : 'gregorian',
  );
  const months = calendar === 'ethiopian' ? recentEthiopianMonths() : recentMonths();
  const [chosen, setChosen] = useState<Record<ExportCalendar, string | undefined>>({
    gregorian: undefined,
    ethiopian: undefined,
  });
  const month = chosen[calendar] ?? months[0] ?? '';
  const label = (value: string) =>
    calendar === 'ethiopian' ? ethiopianMonthName(value, language) : monthName(value, language);

  const send = () =>
    exportOrders.mutate(
      { month, calendar },
      {
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
      },
    );

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
      <Segmented<ExportCalendar>
        label={t('exportSheet.calendar')}
        value={calendar}
        onChange={setCalendar}
        options={[
          { value: 'ethiopian', label: t('exportSheet.ethiopian') },
          { value: 'gregorian', label: t('exportSheet.gregorian') },
        ]}
      />
      <Chips
        label={t('exportSheet.month')}
        value={month}
        onChange={(value) => setChosen((old) => ({ ...old, [calendar]: value }))}
        options={months.map((value) => ({ value, label: label(value) }))}
      />
      <p className={s.muted}>{t('exportSheet.bothCalendars')}</p>
    </Sheet>
  );
}
