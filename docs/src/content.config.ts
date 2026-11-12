import { docsLoader, i18nLoader } from '@astrojs/starlight/loaders';
import { docsSchema, i18nSchema } from '@astrojs/starlight/schema';
import { defineCollection } from 'astro:content';
import { z } from 'astro/zod';

export const collections = {
  i18n: defineCollection({ loader: i18nLoader(), schema: i18nSchema() }),
  docs: defineCollection({
    loader: docsLoader(),
    schema: docsSchema({
      extend: z.object({
        contentType: z.enum([
          'Tutorial', 'How-to', 'Reference', 'Conceptual', 'Troubleshooting', 'Landing',
        ]).optional(),
        contentPlan: z.string().min(1).optional(),
      }),
    }),
  }),
};
