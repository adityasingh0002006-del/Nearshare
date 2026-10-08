-- Safe, repeatable development seed data for NearShare.
-- Seed passwords are explicit non-authenticatable placeholders, not credentials.
-- Replace them with real password hashes only when authentication is implemented.

INSERT INTO dbo.Localities (locality_name, city, state, pincode)
SELECT source.locality_name, source.city, source.state, source.pincode
FROM (VALUES
    (N'Indiranagar', N'Bengaluru', N'Karnataka', N'560038'),
    (N'Koramangala', N'Bengaluru', N'Karnataka', N'560034'),
    (N'Whitefield', N'Bengaluru', N'Karnataka', N'560066')
) AS source(locality_name, city, state, pincode)
WHERE NOT EXISTS
(
    SELECT 1 FROM dbo.Localities AS existing
    WHERE existing.locality_name = source.locality_name
      AND existing.city = source.city
      AND existing.state = source.state
      AND existing.pincode = source.pincode
);
GO

INSERT INTO dbo.Categories (category_name, description)
SELECT source.category_name, source.description
FROM (VALUES
    (N'Tools', N'Hand tools and small home improvement equipment'),
    (N'Electronics', N'Useful consumer electronics and accessories'),
    (N'Outdoor', N'Camping, sports, and outdoor recreation items')
) AS source(category_name, description)
WHERE NOT EXISTS
(
    SELECT 1 FROM dbo.Categories AS existing
    WHERE existing.category_name = source.category_name
);
GO

INSERT INTO dbo.Users
    (full_name, email, phone, password_hash, role, locality_id)
SELECT source.full_name, source.email, source.phone,
       N'DEV_ONLY_PLACEHOLDER_NOT_A_PASSWORD_HASH', N'USER', l.locality_id
FROM (VALUES
    (N'Aarav Sharma', N'aarav.dev@nearshare.example', N'+919900000101', N'Indiranagar', N'560038'),
    (N'Meera Iyer', N'meera.dev@nearshare.example', N'+919900000102', N'Koramangala', N'560034'),
    (N'Kabir Rao', N'kabir.dev@nearshare.example', N'+919900000103', N'Whitefield', N'560066')
) AS source(full_name, email, phone, locality_name, pincode)
INNER JOIN dbo.Localities AS l
    ON l.locality_name = source.locality_name AND l.pincode = source.pincode
WHERE NOT EXISTS
(
    SELECT 1 FROM dbo.Users AS existing WHERE existing.email = source.email
);
GO

INSERT INTO dbo.Items
    (owner_id, category_id, item_name, description, item_condition,
     rental_price, security_deposit, is_available)
SELECT u.user_id, c.category_id, source.item_name, source.description,
       source.item_condition, source.rental_price, source.security_deposit, 1
FROM (VALUES
    (N'aarav.dev@nearshare.example', N'Tools', N'Cordless drill', N'Compact drill for small home projects.', N'GOOD', CONVERT(DECIMAL(10,2), 0), CONVERT(DECIMAL(10,2), 0)),
    (N'meera.dev@nearshare.example', N'Electronics', N'Portable projector', N'Portable projector for a small gathering.', N'LIKE_NEW', CONVERT(DECIMAL(10,2), 250), CONVERT(DECIMAL(10,2), 500)),
    (N'kabir.dev@nearshare.example', N'Outdoor', N'Two-person tent', N'Lightweight tent for weekend camping.', N'GOOD', CONVERT(DECIMAL(10,2), 150), CONVERT(DECIMAL(10,2), 300))
) AS source(owner_email, category_name, item_name, description, item_condition, rental_price, security_deposit)
INNER JOIN dbo.Users AS u ON u.email = source.owner_email
INNER JOIN dbo.Categories AS c ON c.category_name = source.category_name
WHERE NOT EXISTS
(
    SELECT 1 FROM dbo.Items AS existing
    WHERE existing.owner_id = u.user_id AND existing.item_name = source.item_name
);
GO

INSERT INTO dbo.Requests
    (requester_id, category_id, item_description, locality_id,
     start_datetime, end_datetime, max_budget, status)
SELECT u.user_id, c.category_id, source.item_description, l.locality_id,
       source.start_datetime, source.end_datetime, source.max_budget, N'OPEN'
FROM (VALUES
    (N'meera.dev@nearshare.example', N'Tools', N'Looking to borrow a basic toolkit for a weekend shelf installation.', N'Indiranagar', N'560038', CONVERT(DATETIME2(0), '2030-04-12T09:00:00'), CONVERT(DATETIME2(0), '2030-04-14T18:00:00'), CONVERT(DECIMAL(10,2), 100)),
    (N'aarav.dev@nearshare.example', N'Outdoor', N'Need a small tent for a short camping trip.', N'Koramangala', N'560034', CONVERT(DATETIME2(0), '2030-05-02T08:00:00'), CONVERT(DATETIME2(0), '2030-05-04T20:00:00'), CONVERT(DECIMAL(10,2), 500)),
    (N'kabir.dev@nearshare.example', N'Electronics', N'Looking for a projector for a community movie evening.', N'Whitefield', N'560066', CONVERT(DATETIME2(0), '2030-06-15T16:00:00'), CONVERT(DATETIME2(0), '2030-06-15T23:00:00'), CONVERT(DECIMAL(10,2), 400))
) AS source(requester_email, category_name, item_description, locality_name, pincode, start_datetime, end_datetime, max_budget)
INNER JOIN dbo.Users AS u ON u.email = source.requester_email
INNER JOIN dbo.Categories AS c ON c.category_name = source.category_name
INNER JOIN dbo.Localities AS l
    ON l.locality_name = source.locality_name AND l.pincode = source.pincode
WHERE NOT EXISTS
(
    SELECT 1 FROM dbo.Requests AS existing
    WHERE existing.requester_id = u.user_id
      AND existing.item_description = source.item_description
      AND existing.start_datetime = source.start_datetime
);
GO
