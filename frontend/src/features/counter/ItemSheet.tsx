import { useDeferredValue, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAvailability, useProducts } from '@/api/queries';
import type { Product, Variant } from '@/api/types';
import { useLanguage, useStore, useShopWords } from '@/app/hooks';
import { inputClass } from '@/components/classes';
import { Icon } from '@/components/Icon';
import { Badge, Button, Field, Photo, Sheet, Stepper, TextInput } from '@/components/ui';
import { swatch } from '@/lib/colors';
import { money, variantLabel } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { lowestPrice, percentOff, type CounterLine } from '@/state/counterDraft';

import s from './counter.module.css';

interface ItemSheetProps {
  /** Editing a line already in the sale; otherwise a new item. */
  line?: CounterLine;
  onClose: () => void;
  onSave: (line: CounterLine) => void;
}

/** Pick a product, its color and size, how many, and the agreed price (D53). */
export function ItemSheet({ line, onClose, onSave }: ItemSheetProps) {
  const { t } = useTranslation();
  const words = useShopWords();
  const language = useLanguage();
  const { storeId, isOwner, store } = useStore();
  const products = useProducts(storeId);
  const [search, setSearch] = useState('');
  const query = useDeferredValue(search.trim().toLowerCase());
  const [product, setProduct] = useState<Product | null>(() =>
    line
      ? (products.data?.products.find((p) => p.variants.some((v) => v.id === line.variantId)) ??
        null)
      : null,
  );
  const [variantId, setVariantId] = useState<string | null>(line?.variantId ?? null);
  const [quantity, setQuantity] = useState(line?.quantity ?? 1);
  const [price, setPrice] = useState(line?.price ?? '');
  const [heldAccepted, setHeldAccepted] = useState(line?.heldAccepted ?? false);
  const availability = useAvailability(storeId, variantId);

  const variant: Variant | undefined = product?.variants.find((v) => v.id === variantId);
  const listed =
    variant?.price === null || variant?.price === undefined ? null : Number(variant.price);
  const staffPercent = isOwner ? null : Number(store.staff_discount_percent);
  const lowest = listed === null ? 0 : lowestPrice(listed, staffPercent);
  const typed = price.trim() === '' ? null : Number(price);
  const agreed = typed ?? listed ?? 0;
  const priceError =
    listed === null
      ? null
      : typed !== null && (Number.isNaN(typed) || typed > listed)
        ? t('counter.aboveListed')
        : agreed < lowest
          ? t('counter.belowLimit', { percent: staffPercent })
          : null;

  const info = availability.data;
  const held = info && info.holds.length > 0 && quantity > info.available;
  const hold = info?.holds[0];
  const canSave =
    variant !== undefined &&
    listed !== null &&
    !priceError &&
    quantity >= 1 &&
    quantity <= variant.stock &&
    (!held || heldAccepted);

  const shown = useMemo(() => {
    const list = products.data?.products ?? [];
    if (!query) return list;
    return list.filter((p) =>
      query
        .split(/\s+/)
        .every((word) =>
          [p.name, p.brand, p.code, p.search_keywords].join(' ').toLowerCase().includes(word),
        ),
    );
  }, [products.data, query]);

  const save = () => {
    if (!product || !variant || listed === null) return;
    onSave({
      variantId: variant.id,
      productName: product.name,
      label: variantLabel(variant.color, variant.size),
      color: variant.color,
      photoUrl: product.photo_url,
      quantity,
      listed,
      price: typed === null || typed === listed ? '' : String(typed),
      available: info?.available ?? variant.stock,
      stock: variant.stock,
      heldAccepted: Boolean(held && heldAccepted),
    });
  };

  // Step 1: choose the product.
  if (!product) {
    return (
      <Sheet open onClose={onClose} label={t('counter.chooseProduct')}>
        <p className={s.sheetTitle}>{t('counter.chooseProduct')}</p>
        <input
          className={inputClass}
          type="search"
          autoFocus
          value={search}
          placeholder={t('counter.searchProduct')}
          aria-label={t('counter.searchProduct')}
          onChange={(event) => setSearch(event.target.value)}
          style={{ marginBottom: 8 }}
        />
        {shown.map((p) => (
          <button
            key={p.id}
            type="button"
            className={s.productPick}
            disabled={p.total_stock === 0}
            onClick={() => {
              haptic.select();
              setProduct(p);
              const inStock = p.variants.filter((v) => v.stock > 0);
              if (inStock.length === 1) setVariantId(inStock[0]?.id ?? null);
            }}
          >
            <Photo url={p.photo_url} size={44} alt={p.name} />
            <span className={s.lineMain}>
              <span className={s.lineName}>{p.name}</span>
              <span className={s.lineSub}>
                {[p.code, money(p.price_min, language)].filter(Boolean).join(' · ')}
              </span>
            </span>
            {p.total_stock === 0 ? (
              <Badge tone="danger">{t('products.soldOut')}</Badge>
            ) : (
              <Icon name="chevronRight" />
            )}
          </button>
        ))}
      </Sheet>
    );
  }

  // Step 2: color and size, quantity, price.
  const only = product.variants.length === 1 ? product.variants[0] : undefined;
  const plain = only !== undefined && !only.color && !only.size;
  return (
    <Sheet
      open
      onClose={onClose}
      label={product.name}
      footer={
        <Button variant="primary" block disabled={!canSave} onClick={save}>
          {line ? t('counter.update') : t('counter.add')}
        </Button>
      }
    >
      <div className={s.line} style={{ borderBottom: 'none', paddingTop: 0 }}>
        <Photo url={product.photo_url} size={48} alt={product.name} />
        <span className={s.lineMain}>
          <span className={s.lineName}>{product.name}</span>
          <span className={s.lineSub}>{product.code}</span>
        </span>
        {!line && (
          <Button variant="link" onClick={() => setProduct(null)}>
            {t('counter.chooseProduct')}
          </Button>
        )}
      </div>

      {plain ? (
        <p className={s.lineSub} style={{ margin: '4px 0 14px' }}>
          {t('counter.inStock', { count: product.variants[0]?.stock ?? 0 })}
        </p>
      ) : (
        <>
          <p className={s.lineSub} style={{ fontWeight: 600, margin: '4px 0 8px' }}>
            {t('counter.chooseVariant', words)}
          </p>
          <div
            className={s.variants}
            role="radiogroup"
            aria-label={t('counter.chooseVariant', words)}
          >
            {product.variants.map((v) => (
              <button
                key={v.id}
                type="button"
                role="radio"
                aria-checked={v.id === variantId}
                className={`${s.variant} ${v.id === variantId ? s.variantOn : ''}`}
                disabled={v.stock === 0}
                onClick={() => {
                  haptic.select();
                  setVariantId(v.id);
                  setQuantity(1);
                  setHeldAccepted(false);
                }}
              >
                <span className={s.variantLabel}>
                  {v.color && <span className={s.dot} style={{ background: swatch(v.color) }} />}
                  {variantLabel(v.color, v.size) || t('grid.noSize', words)}
                </span>
                <span className={s.variantStock}>
                  {v.stock > 0
                    ? t('counter.inStock', { count: v.stock })
                    : t('counter.noneInStock')}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {variant && (
        <>
          <div className={s.qtyRow}>
            <span>{t('counter.quantity')}</span>
            <Stepper
              label={t('counter.quantity')}
              value={quantity}
              min={1}
              max={variant.stock}
              onChange={setQuantity}
            />
          </div>

          {held && hold && (
            <div className={s.held} role="alert">
              <Icon name="warning" size={22} />
              <div>
                <p className={s.heldTitle}>{t('counter.heldTitle')}</p>
                <p className={s.heldBody}>
                  {t('counter.heldBody', {
                    label: variantLabel(variant.color, variant.size),
                    order: hold.order_number,
                    minutes: hold.minutes_left,
                  })}
                </p>
                {!heldAccepted ? (
                  <Button variant="dangerOutline" onClick={() => setHeldAccepted(true)}>
                    {t('counter.sellAnyway')}
                  </Button>
                ) : (
                  <Badge tone="warn">{t('counter.sellAnyway')} ✓</Badge>
                )}
              </div>
            </div>
          )}

          <div className={s.priceRow}>
            <span>{t('counter.listedPrice')}</span>
            <span className={s.listed}>{money(listed, language)}</span>
          </div>
          <Field
            label={t('counter.agreedPrice')}
            error={priceError ?? undefined}
            hint={
              staffPercent !== null && listed !== null
                ? t('counter.lowestForYou', { price: money(lowest, language) })
                : t('counter.agreedHint')
            }
          >
            {(id) => (
              <TextInput
                id={id}
                inputMode="decimal"
                value={price}
                placeholder={listed !== null ? String(listed) : ''}
                invalid={Boolean(priceError)}
                onChange={(event) => setPrice(event.target.value.replace(/[^\d.]/g, ''))}
              />
            )}
          </Field>
          {listed !== null && agreed < listed && !priceError && (
            <Badge tone="success">
              {t('counter.off', { percent: percentOff(listed, agreed) })}
            </Badge>
          )}
        </>
      )}
    </Sheet>
  );
}
