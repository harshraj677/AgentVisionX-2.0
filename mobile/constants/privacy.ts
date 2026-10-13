const LABELS: Record<string, string> = {
  API_KEY: 'API key / token',
  PRIVATE_KEY: 'Private key',
  PASSWORD_OR_SECRET: 'Password / secret',
  EMAIL_ADDRESS: 'Email address',
  PHONE_NUMBER: 'Phone number',
  CREDIT_CARD: 'Credit card',
  US_SSN: 'US SSN',
  US_PASSPORT: 'Passport number',
  US_ITIN: 'US ITIN',
  US_BANK_NUMBER: 'Bank account number',
  IN_AADHAAR: 'Aadhaar number',
  IN_PAN: 'PAN number',
  PERSON: 'Person name',
  IP_ADDRESS: 'IP address',
  IBAN_CODE: 'Bank account (IBAN)',
  MEDICAL_LICENSE: 'Medical license',
};

/** Human-readable name for a detected category. Never contains the matched value. */
export const categoryLabel = (category: string) => LABELS[category] ?? category.replace(/_/g, ' ').toLowerCase();

export const categoryList = (categories: Record<string, number> | string[] | undefined) =>
  (Array.isArray(categories) ? categories : Object.keys(categories ?? {})).map(categoryLabel).join(', ');
