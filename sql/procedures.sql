-- Return currently available items in the requested category.
CREATE OR ALTER PROCEDURE dbo.GetAvailableItemsByCategory
    @CategoryId INT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        item_id,
        item_name,
        item_description,
        item_condition,
        rental_price,
        security_deposit,
        category_id,
        category_name,
        owner_id,
        owner_name,
        locality_id,
        locality_name,
        city,
        state,
        pincode
    FROM dbo.AvailableItemsView
    WHERE category_id = @CategoryId
    ORDER BY item_name, item_id;
END;
GO
