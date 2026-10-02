import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useProduct, useSetStock } from '@/api/queries';
import type { Product } from '@/api/types';
import { useErrorText, useStore } from '@/app/hooks';
import { Badge, Button, Photo, Sheet, Stepper } from '@/components/ui';
import { swatch } from '@/lib/colors';
import { variantLabel } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { sortSizes } from '@/state/gridDraft';
import { toast } from '@/state/toasts';

import s from './products.module.css';

/** Design: "Quick stock edit": change several stock numbers, save once. */
export function QuickStockSheet({
  product: listed,
  onClose,
}: {
  product: Product;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useErrorText();
  const { storeId } = useStore();
  const product = useProduct(storeId, listed.id).data ?? listed;
  const save = useSetStock(storeId, product.id);
  const [draft, setDraft] = useState<Record<string, number>>({});

  const variants = useMemo(() => {
    const order = sortSizes(product.variants.map((v) => v.size ?? ''));
    return [...product.variants].sort(
      (a, b) =>
        (a.color ?? '').localeCompare(b.color ?? '') ||
        order.indexOf(a.size ?? '') - order.indexOf(b.size ?? ''),
    );
  }, [product.variants]);

  const changes = variants
    .filter((v) => draft[v.id] !== undefined && draft[v.id] !== v.stock)
    .map((v) => ({ variantId: v.id, stock: draft[v.id] as number }));

  const submit = () =>
    save.mutate(changes, {
      onSuccess: () => {
        haptic.success();
        toast.info(t('common.saved'));
        onClose();
      },
      onError: (error) => {
        haptic.error();
        toast.error(errorText(error));
      },
    });

  return (
    <Sheet
      open
      onClose={onClose}
      label={t('products.quickStock')}
      footer={
        variants.length === 0 ? (
          <Button
            variant="primary"
            block
            onClick={() => navigate(`/s/${storeId}/products/${product.id}/stock`)}
          >
            {t('products.addVariants')}
          </Button>
        ) : (
          <Button
            variant="primary"
            block
            busy={save.isPending}
            disabled={changes.length === 0}
            onClick={submit}
          >
            {t('products.saveStock')}
          </Button>
        )
      }
    >
      <div className={s.sheetHead}>
        <Photo url={product.photo_url} size={48} alt={product.name} />
        <div>
          <div className={s.sheetTitle}>{product.name}</div>
          <div className={s.sheetSub}>{t('products.quickStock')}</div>
        </div>
      </div>

      {variants.length === 0 && (
        <p style={{ color: 'var(--text-muted)' }}>{t('products.noVariantsHint')}</p>
      )}
      {variants.map((variant) => {
        const value = draft[variant.id] ?? variant.stock;
        const label = variantLabel(variant.color, variant.size) || t('grid.noSize');
        return (
          <div key={variant.id} className={s.stockRow}>
            {variant.color && (
              <span className={s.dot} style={{ background: swatch(variant.color) }} />
            )}
            <span className={s.stockLabel}>{label}</span>
            {value === 0 && <Badge tone="danger">{t('products.soldOut')}</Badge>}
            <Stepper
              label={label}
              value={value}
              onChange={(next) => setDraft((d) => ({ ...d, [variant.id]: next }))}
            />
          </div>
        );
      })}

      <div className={s.sheetActions}>
        <Button onClick={() => navigate(`/s/${storeId}/products/${product.id}`)}>
          {t('products.editDetails')}
        </Button>
        <Button onClick={() => navigate(`/s/${storeId}/products/${product.id}/stock`)}>
          {product.variants.length ? t('products.stockAndPrices') : t('products.addVariants')}
        </Button>
      </div>
    </Sheet>
  );
}
