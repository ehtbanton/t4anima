// Intentionally empty by default.
// Add Drizzle tables here when the site actually needs a database.
// See examples/d1/db/schema.ts for an opt-in example.
import {sqliteTable,text} from 'drizzle-orm/sqlite-core';
export const reviews=sqliteTable('reviews',{id:text('id').primaryKey(),patientId:text('patient_id').notNull(),status:text('status').notNull(),note:text('note').notNull(),assignee:text('assignee').notNull(),due:text('due').notNull(),updatedAt:text('updated_at').notNull()});
