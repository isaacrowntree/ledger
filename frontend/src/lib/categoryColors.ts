// Spending categories wear one fixed colour everywhere -- the overview's bars,
// trends, budget meters and the transaction list -- so a category is
// recognised before its label is read. Colour follows the category, never its
// rank: a filter that hides some categories does not repaint the rest.
//
// Eight groups take the eight validated categorical slots (dataviz reference
// palette, light and dark steps defined in styles.css). Everything else is
// neutral, so the colours keep their meaning.

const GROUPS: Record<string, string> = {
  "Eating Out": "eat",
  Coffee: "eat",
  Groceries: "groceries",
  Shopping: "shopping",
  Clothing: "shopping",
  Electronics: "shopping",
  "Home & Garden": "shopping",
  Car: "car",
  Fuel: "car",
  Tolls: "car",
  Mortgage: "housing",
  Strata: "housing",
  Rent: "housing",
  "Rental: Maintenance": "housing",
  "Rental: Other": "housing",
  "Rental: Capital Works": "housing",
  Utilities: "utilities",
  Travel: "travel",
  "Public Transport": "travel",
  Health: "health",
  Insurance: "health",
  Fitness: "health",
};

export function categoryGroup(category: string | null | undefined): string {
  return (category && GROUPS[category]) || "other";
}

/** CSS colour for a category (a custom property that switches with the theme). */
export function categoryColor(category: string | null | undefined): string {
  return `var(--cat-${categoryGroup(category)})`;
}
