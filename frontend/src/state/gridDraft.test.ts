import { jacket } from '@/test/fixtures';

import { cellKey, sortSizes, useGridDraft } from './gridDraft';

const grid = () => useGridDraft.getState();

describe('the stock grid draft', () => {
  beforeEach(() => grid().load(jacket()));

  it('loads colors, sizes in clothing order, and cells', () => {
    expect(grid().colors).toEqual(['Blue']);
    expect(grid().sizes).toEqual(['M', 'XL']);
    expect(grid().cells[cellKey('Blue', 'XL')]).toEqual({
      id: 'v-blue-xl',
      stock: 1,
      price: '3800',
    });
    expect(grid().dirty).toBe(false);
  });

  it('adds a color and a size, and a new cell when an empty box is tapped', () => {
    expect(grid().addColor('Black')).toBe(true);
    expect(grid().addColor(' black ')).toBe(false); // already there
    expect(grid().addSize('S')).toBe(true);
    expect(grid().sizes).toEqual(['S', 'M', 'XL']);
    grid().select(cellKey('Black', 'S'));
    grid().setStock(cellKey('Black', 'S'), 4);
    expect(grid().rows()).toContainEqual({ color: 'Black', size: 'S', stock: 4, price: null });
  });

  it('never goes below 0 and keeps the own price as typed', () => {
    grid().setStock(cellKey('Blue', 'M'), -3);
    grid().setPrice(cellKey('Blue', 'M'), '3600');
    expect(grid().rows()).toContainEqual({
      id: 'v-blue-m',
      color: 'Blue',
      size: 'M',
      stock: 0,
      price: '3600',
    });
  });

  it('removes a variant on save, but re-adding it before saving keeps it', () => {
    grid().removeCell(cellKey('Blue', 'XL'));
    expect(grid().removedIds()).toEqual(['v-blue-xl']);
    grid().select(cellKey('Blue', 'XL'));
    expect(grid().removedIds()).toEqual([]);
    expect(grid().cells[cellKey('Blue', 'XL')]?.id).toBe('v-blue-xl');
  });
});

describe('sortSizes', () => {
  it('orders clothing sizes, then shoe sizes, then anything else', () => {
    expect(sortSizes(['XL', '42', 'S', 'One size', '40', 'M'])).toEqual([
      'S',
      'M',
      'XL',
      '40',
      '42',
      'One size',
    ]);
  });
});
