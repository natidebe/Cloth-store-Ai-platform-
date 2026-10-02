import { useDeferredValue, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useAnalytics, useProducts } from '@/api/queries';
import type { Product } from '@/api/types';
import { LoadFailed } from '@/app/AccessScreens';
import { useLanguage, useStore } from '@/app/hooks';
import { StoreTabs } from '@/app/StoreShell';
import { inputClass } from '@/components/classes';
import { Icon } from '@/components/Icon';
import {
  Badge,
  BottomBar,
  Button,
  Centered,
  Chips,
  Page,
  PageHeader,
  Photo,
  SkeletonList,
} from '@/components/ui';
import { capitalize, priceRange } from '@/lib/format';

import s from './products.module.css';
import { QuickStockSheet } from './QuickStockSheet';

const ALL = '__all__';
const LOW_STOCK_AT = 2;

/** Design: "Staff products" / "Products, dark" / "Products, Amharic". */
export function ProductsScreen() {
  const { t } = useTranslation();
  const language = useLanguage();
  const navigate = useNavigate();
  const { storeId, role } = useStore();
  const products = useProducts(storeId);
  const today = useAnalytics(storeId, 'today');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<string>(ALL);
  const [open, setOpen] = useState<Product | null>(null);
  const query = useDeferredValue(search.trim().toLowerCase());

  const shown = useMemo(() => {
    const list = products.data?.products ?? [];
    return list.filter((product) => {
      if (category !== ALL && product.category !== category) return false;
      if (!query) return true;
      const text = [product.name, product.brand, product.code, product.search_keywords]
        .join(' ')
        .toLowerCase();
      return query.split(/\s+/).every((word) => text.includes(word));
    });
  }, [products.data, category, query]);

  const all = products.data?.products ?? [];
  const lowStock = all.filter((p) => p.variants.some((v) => v.stock <= LOW_STOCK_AT)).length;
  const addProduct = (
    <BottomBar>
      <Button variant="primary" block onClick={() => navigate(`/s/${storeId}/products/new`)}>
        {t('products.addProduct')}
      </Button>
    </BottomBar>
  );

  if (products.isError)
    return <LoadFailed error={products.error} onRetry={() => void products.refetch()} />;

  return (
    <Page>
      <PageHeader title={t('products.title')} role={role} />
      <StoreTabs />

      {products.isPending ? (
        <SkeletonList />
      ) : all.length === 0 ? (
        <Centered icon="hanger" title={t('products.emptyTitle')} body={t('products.emptyBody')} />
      ) : (
        <>
          <div className={s.stats}>
            <Stat value={all.length} label={t('products.statProducts')} />
            <Stat value={lowStock} label={t('products.statLowStock')} warn />
            <Stat value={today.data?.orders_placed ?? '–'} label={t('products.statOrdersToday')} />
          </div>

          <div className={s.search}>
            <Icon name="search" size={18} className={s.searchIcon} />
            <input
              className={`${inputClass} ${s.searchInput}`}
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t('products.search')}
              aria-label={t('products.search')}
            />
          </div>

          {products.data.categories.length > 0 && (
            <Chips
              label={t('product.category')}
              value={category}
              onChange={setCategory}
              options={[
                { value: ALL, label: t('products.all') },
                ...products.data.categories.map((c) => ({ value: c, label: capitalize(c) })),
              ]}
            />
          )}

          {shown.length === 0 ? (
            <p style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
              {t('products.noMatch')}
            </p>
          ) : (
            <div className={s.list}>
              {shown.map((product) => (
                <button
                  key={product.id}
                  type="button"
                  className={s.product}
                  onClick={() => setOpen(product)}
                >
                  <Photo url={product.photo_url} size={56} alt={product.name} />
                  <span className={s.productMain}>
                    <span className={s.productName} style={{ display: 'block' }}>
                      {product.name}
                    </span>
                    <span className={s.productMeta} style={{ display: 'block' }}>
                      {[product.code, product.category && capitalize(product.category)]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                    <span className={s.productPrice}>
                      {priceRange(product.price_min, product.price_max, language)}
                    </span>
                  </span>
                  <span className={s.productEnd}>
                    <span className={s.stockNumber}>{product.total_stock}</span>
                    {product.total_stock === 0 ? (
                      <Badge tone="danger">{t('products.soldOut')}</Badge>
                    ) : (
                      product.low_stock && <Badge tone="warn">{t('products.lowStock')}</Badge>
                    )}
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {addProduct}
      {open && <QuickStockSheet product={open} onClose={() => setOpen(null)} />}
    </Page>
  );
}

function Stat({ value, label, warn }: { value: number | string; label: string; warn?: boolean }) {
  return (
    <div className={s.stat}>
      <div className={`${s.statValue} ${warn ? s.statWarn : ''}`}>{value}</div>
      <div className={s.statLabel}>{label}</div>
    </div>
  );
}
