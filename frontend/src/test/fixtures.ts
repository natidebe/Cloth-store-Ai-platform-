import type {
  Analytics,
  Me,
  Order,
  Product,
  ShopType,
  ShopTypeInfo,
  StoreSettings,
} from '@/api/types';

export const STORE_ID = '6f1c1d2e-1111-4222-8333-444455556666';

/** Every shop type with its words, as the server sends them (Phase 13). */
export function shopTypes(): ShopTypeInfo[] {
  const label = (en: string, am: string, plural: string, icon: string) => ({
    en,
    am,
    plural,
    icon,
  });
  return [
    {
      type: 'clothing',
      names: { en: 'Clothing & shoes', am: 'አልባሳትና ጫማ' },
      option1: label('Color', 'ቀለም', 'Colors', '🎨'),
      option2: label('Size', 'ቁጥር', 'Sizes', '📏'),
      categories: ['Clothing', 'Shoes', 'Bags', 'Accessories'],
      condition_and_warranty: false,
    },
    {
      type: 'electronics',
      names: { en: 'Electronics & phones', am: 'ኤሌክትሮኒክስና ስልክ' },
      option1: label('Color', 'ቀለም', 'Colors', '🎨'),
      option2: label('Storage', 'ማከማቻ', 'Storage', '💾'),
      categories: ['Phones', 'Laptops', 'Tablets', 'Accessories'],
      condition_and_warranty: true,
    },
    {
      type: 'cosmetics',
      names: { en: 'Cosmetics & perfume', am: 'መዋቢያና ሽቶ' },
      option1: label('Shade', 'ቀለም', 'Shades', '🎨'),
      option2: label('Volume', 'መጠን', 'Volumes', '🧴'),
      categories: ['Makeup', 'Perfume', 'Skincare', 'Hair'],
      condition_and_warranty: false,
    },
    {
      type: 'general',
      names: { en: 'General', am: 'ሌላ' },
      option1: label('Type', 'አይነት', 'Types', '🏷️'),
      option2: label('Size', 'መጠን', 'Sizes', '📏'),
      categories: [],
      condition_and_warranty: false,
    },
  ];
}

/** A shop's words for /me (the type's, no renames). */
export function shopWordsOf(type: ShopType) {
  const info = shopTypes().find((t) => t.type === type)!;
  return {
    shop_type: type,
    option1: info.option1,
    option2: info.option2,
    categories: info.categories,
    condition_and_warranty: info.condition_and_warranty,
  };
}

export function me(role: Me['role'] = 'owner', type: ShopType = 'clothing'): Me {
  return {
    user: { id: 42, name: 'Nati', username: 'nati', language_code: 'en' },
    role,
    store: {
      id: STORE_ID,
      name: 'nati fashion',
      status: 'active',
      plan: 'free',
      bot_username: 'nati_fashion_bot',
      staff_group_linked: true,
      channel_linked: false,
      staff_discount_percent: '10',
      payment_methods: ['Cash', 'Telebirr'],
      ...shopWordsOf(type),
    },
  };
}

export function jacket(): Product {
  return {
    id: 'p-jacket',
    code: 'P102',
    name: 'Classic Denim Jacket',
    brand: "Levi's",
    category: 'clothing',
    base_price: 3500,
    photo_url: null,
    description: 'Classic fit',
    search_keywords: 'jacket, ጃኬት',
    condition: null,
    warranty_months: null,
    total_stock: 6,
    variant_count: 2,
    on_sale: true,
    low_stock: true,
    price_min: '3500',
    price_max: '3800',
    variants: [
      { id: 'v-blue-m', color: 'Blue', size: 'M', stock: 5, price_override: null, price: '3500' },
      { id: 'v-blue-xl', color: 'Blue', size: 'XL', stock: 1, price_override: 3800, price: '3800' },
    ],
  };
}

export function sneaker(): Product {
  return {
    ...jacket(),
    id: 'p-nike',
    code: 'P105',
    name: 'Nike Air Max 90',
    brand: 'Nike',
    category: 'sneakers',
    base_price: 6500,
    search_keywords: 'nike, ጫማ',
    total_stock: 0,
    low_stock: true,
    price_min: '6500',
    price_max: '6500',
    variants: [
      {
        id: 'v-white-42',
        color: 'White',
        size: '42',
        stock: 0,
        price_override: null,
        price: '6500',
      },
    ],
  };
}

export function analytics(): Analytics {
  return {
    period: 'today',
    from: '2026-10-02T00:00:00+03:00',
    to: '2026-10-03T00:00:00+03:00',
    revenue: '14500',
    payments: 2,
    average_order: '7250.00',
    orders_placed: 3,
    orders_paid: 2,
    paid_rate: 0.667,
    unpaid_orders: 1,
    delivery_orders: 1,
    pickup_orders: 2,
    new_customers: 2,
    per_day: [{ day: '2026-10-02', placed: 3, paid: 2, revenue: '14500' }],
    top_products: [
      {
        product_id: 'p-jacket',
        name: 'Classic Denim Jacket',
        code: 'P102',
        quantity: 3,
        revenue: '10500',
      },
    ],
    low_stock: [],
    ai_calls_today: 4,
    ai_daily_limit: 300,
    telegram_orders: 2,
    in_shop_sales: 1,
    in_shop_revenue: '4500',
    discount_total: '500',
    discounted_items: 1,
    sellers: [{ telegram_id: 7, name: 'Sara', sales: 1, revenue: '4500', discount: '500' }],
  };
}

export function order(): Order {
  return {
    id: 'o-1',
    number: 'AB12CD',
    status: 'pending',
    payment_status: 'unpaid',
    total: 7300,
    currency: 'ETB',
    fulfillment: 'delivery',
    customer: { name: 'Abebe', phone: '0911223344' },
    delivery_address: null,
    created_at: '2026-10-02T09:00:00+00:00',
    items: [
      {
        name: 'Classic Denim Jacket',
        code: 'P102',
        color: 'Blue',
        size: 'M',
        quantity: 2,
        price: 3650,
        list_price: 3650,
      },
    ],
    channel: 'telegram',
    payment_method: null,
    payment_note: null,
    sold_by: null,
    note: null,
  };
}

export function settings(): StoreSettings {
  return {
    payment_accounts: [{ name: 'Telebirr', number: '0911 000 000' }],
    delivery_areas: [{ area: 'Bole', fee: 150 }],
    opening_week: null,
    location: 'Bole, Edna Mall',
    pickup_instructions: null,
    return_policy: null,
    staff_discount_percent: '10',
    shop_type: 'clothing',
    option_labels: null,
    shop_types: shopTypes(),
    payment_instructions: 'Telebirr: 0911 000 000',
    delivery_info: 'Bole: 150 ETB',
    opening_hours: null,
  };
}
