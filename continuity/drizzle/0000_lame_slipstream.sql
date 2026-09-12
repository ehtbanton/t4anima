CREATE TABLE `reviews` (
	`id` text PRIMARY KEY NOT NULL,
	`patient_id` text NOT NULL,
	`status` text NOT NULL,
	`note` text NOT NULL,
	`assignee` text NOT NULL,
	`due` text NOT NULL,
	`updated_at` text NOT NULL
);
