-- Available items with their category, owner, and the owner's locality.
-- Locality is used instead of exposing an exact address or GPS position.
CREATE OR ALTER VIEW dbo.AvailableItemsView
AS
    SELECT
        i.item_id,
        i.item_name,
        i.description AS item_description,
        i.item_condition,
        i.rental_price,
        i.security_deposit,
        i.is_available,
        i.created_at AS item_created_at,
        c.category_id,
        c.category_name,
        u.user_id AS owner_id,
        u.full_name AS owner_name,
        l.locality_id,
        l.locality_name,
        l.city,
        l.state,
        l.pincode
    FROM dbo.Items AS i
    INNER JOIN dbo.Categories AS c ON c.category_id = i.category_id
    INNER JOIN dbo.Users AS u ON u.user_id = i.owner_id
    INNER JOIN dbo.Localities AS l ON l.locality_id = u.locality_id
    WHERE i.is_available = 1 AND u.is_active = 1;
GO
