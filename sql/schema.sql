-- NearShare Azure SQL foundation schema.
-- Run this script once in the target database before views, procedures, and seed data.
-- All timestamps are UTC. Updated timestamps are maintained by the application when records change.
-- No exact home address or GPS location is stored; locality is the most specific location here.

CREATE TABLE dbo.Localities
(
    locality_id INT IDENTITY(1,1) NOT NULL,
    locality_name NVARCHAR(100) NOT NULL,
    city NVARCHAR(100) NOT NULL,
    state NVARCHAR(100) NOT NULL,
    pincode NVARCHAR(10) NOT NULL,
    CONSTRAINT PK_Localities PRIMARY KEY (locality_id),
    CONSTRAINT UQ_Localities_NameCityStatePincode
        UNIQUE (locality_name, city, state, pincode)
);
GO

CREATE TABLE dbo.Categories
(
    category_id INT IDENTITY(1,1) NOT NULL,
    category_name NVARCHAR(100) NOT NULL,
    description NVARCHAR(500) NULL,
    CONSTRAINT PK_Categories PRIMARY KEY (category_id),
    CONSTRAINT UQ_Categories_CategoryName UNIQUE (category_name)
);
GO

CREATE TABLE dbo.Users
(
    user_id INT IDENTITY(1,1) NOT NULL,
    full_name NVARCHAR(150) NOT NULL,
    email NVARCHAR(254) NOT NULL,
    phone NVARCHAR(25) NOT NULL,
    password_hash NVARCHAR(255) NOT NULL,
    role NVARCHAR(10) NOT NULL
        CONSTRAINT DF_Users_Role DEFAULT (N'USER'),
    locality_id INT NOT NULL,
    is_active BIT NOT NULL
        CONSTRAINT DF_Users_IsActive DEFAULT (1),
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Users_CreatedAt DEFAULT (SYSUTCDATETIME()),
    updated_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Users_UpdatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_Users PRIMARY KEY (user_id),
    CONSTRAINT UQ_Users_Email UNIQUE (email),
    CONSTRAINT UQ_Users_Phone UNIQUE (phone),
    CONSTRAINT CK_Users_Role CHECK (role IN (N'USER', N'ADMIN')),
    CONSTRAINT FK_Users_Localities FOREIGN KEY (locality_id)
        REFERENCES dbo.Localities (locality_id)
);
GO

CREATE TABLE dbo.Items
(
    item_id INT IDENTITY(1,1) NOT NULL,
    owner_id INT NOT NULL,
    category_id INT NOT NULL,
    item_name NVARCHAR(150) NOT NULL,
    description NVARCHAR(1000) NULL,
    item_condition NVARCHAR(30) NOT NULL,
    rental_price DECIMAL(10,2) NOT NULL
        CONSTRAINT DF_Items_RentalPrice DEFAULT (0),
    security_deposit DECIMAL(10,2) NOT NULL
        CONSTRAINT DF_Items_SecurityDeposit DEFAULT (0),
    is_available BIT NOT NULL
        CONSTRAINT DF_Items_IsAvailable DEFAULT (1),
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Items_CreatedAt DEFAULT (SYSUTCDATETIME()),
    updated_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Items_UpdatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_Items PRIMARY KEY (item_id),
    CONSTRAINT CK_Items_Condition CHECK
        (item_condition IN (N'NEW', N'LIKE_NEW', N'GOOD', N'FAIR', N'POOR')),
    CONSTRAINT CK_Items_RentalPrice CHECK (rental_price >= 0),
    CONSTRAINT CK_Items_SecurityDeposit CHECK (security_deposit >= 0),
    CONSTRAINT FK_Items_Users FOREIGN KEY (owner_id) REFERENCES dbo.Users (user_id),
    CONSTRAINT FK_Items_Categories FOREIGN KEY (category_id) REFERENCES dbo.Categories (category_id)
);
GO

CREATE TABLE dbo.ItemImages
(
    image_id INT IDENTITY(1,1) NOT NULL,
    item_id INT NOT NULL,
    blob_url NVARCHAR(2048) NOT NULL,
    uploaded_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_ItemImages_UploadedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_ItemImages PRIMARY KEY (image_id),
    CONSTRAINT FK_ItemImages_Items FOREIGN KEY (item_id) REFERENCES dbo.Items (item_id)
);
GO

CREATE TABLE dbo.Requests
(
    request_id INT IDENTITY(1,1) NOT NULL,
    requester_id INT NOT NULL,
    category_id INT NOT NULL,
    item_description NVARCHAR(1000) NOT NULL,
    locality_id INT NOT NULL,
    start_datetime DATETIME2(0) NOT NULL,
    end_datetime DATETIME2(0) NOT NULL,
    max_budget DECIMAL(10,2) NOT NULL,
    status NVARCHAR(12) NOT NULL
        CONSTRAINT DF_Requests_Status DEFAULT (N'OPEN'),
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Requests_CreatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_Requests PRIMARY KEY (request_id),
    CONSTRAINT CK_Requests_DateRange CHECK (start_datetime < end_datetime),
    CONSTRAINT CK_Requests_MaxBudget CHECK (max_budget >= 0),
    CONSTRAINT CK_Requests_Status CHECK
        (status IN (N'OPEN', N'MATCHED', N'BOOKED', N'CANCELLED', N'COMPLETED')),
    CONSTRAINT FK_Requests_Users FOREIGN KEY (requester_id) REFERENCES dbo.Users (user_id),
    CONSTRAINT FK_Requests_Categories FOREIGN KEY (category_id) REFERENCES dbo.Categories (category_id),
    CONSTRAINT FK_Requests_Localities FOREIGN KEY (locality_id) REFERENCES dbo.Localities (locality_id)
);
GO

CREATE TABLE dbo.Offers
(
    offer_id INT IDENTITY(1,1) NOT NULL,
    request_id INT NOT NULL,
    item_id INT NOT NULL,
    owner_id INT NOT NULL,
    offer_type NVARCHAR(20) NOT NULL,
    offered_price DECIMAL(10,2) NOT NULL,
    security_deposit DECIMAL(10,2) NOT NULL
        CONSTRAINT DF_Offers_SecurityDeposit DEFAULT (0),
    message NVARCHAR(1000) NULL,
    status NVARCHAR(10) NOT NULL
        CONSTRAINT DF_Offers_Status DEFAULT (N'PENDING'),
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Offers_CreatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_Offers PRIMARY KEY (offer_id),
    CONSTRAINT CK_Offers_Type CHECK (offer_type IN (N'FREE_LENDING', N'RENTAL')),
    CONSTRAINT CK_Offers_Status CHECK
        (status IN (N'PENDING', N'ACCEPTED', N'REJECTED', N'WITHDRAWN', N'EXPIRED')),
    CONSTRAINT CK_Offers_OfferedPrice CHECK (offered_price >= 0),
    CONSTRAINT CK_Offers_SecurityDeposit CHECK (security_deposit >= 0),
    CONSTRAINT CK_Offers_FreePrice CHECK (offer_type <> N'FREE_LENDING' OR offered_price = 0),
    CONSTRAINT FK_Offers_Requests FOREIGN KEY (request_id) REFERENCES dbo.Requests (request_id),
    CONSTRAINT FK_Offers_Items FOREIGN KEY (item_id) REFERENCES dbo.Items (item_id),
    CONSTRAINT FK_Offers_Users FOREIGN KEY (owner_id) REFERENCES dbo.Users (user_id)
);
GO

CREATE TABLE dbo.Bookings
(
    booking_id INT IDENTITY(1,1) NOT NULL,
    offer_id INT NOT NULL,
    borrower_id INT NOT NULL,
    start_datetime DATETIME2(0) NOT NULL,
    end_datetime DATETIME2(0) NOT NULL,
    agreed_price DECIMAL(10,2) NOT NULL,
    security_deposit DECIMAL(10,2) NOT NULL
        CONSTRAINT DF_Bookings_SecurityDeposit DEFAULT (0),
    status NVARCHAR(12) NOT NULL
        CONSTRAINT DF_Bookings_Status DEFAULT (N'BOOKED'),
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Bookings_CreatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_Bookings PRIMARY KEY (booking_id),
    -- One accepted offer can create at most one booking (Offers 1:0..1 Bookings).
    CONSTRAINT UQ_Bookings_Offer UNIQUE (offer_id),
    CONSTRAINT CK_Bookings_DateRange CHECK (start_datetime < end_datetime),
    CONSTRAINT CK_Bookings_AgreedPrice CHECK (agreed_price >= 0),
    CONSTRAINT CK_Bookings_SecurityDeposit CHECK (security_deposit >= 0),
    CONSTRAINT CK_Bookings_Status CHECK
        (status IN (N'BOOKED', N'HANDED_OVER', N'RETURNED', N'COMPLETED', N'CANCELLED')),
    CONSTRAINT FK_Bookings_Offers FOREIGN KEY (offer_id) REFERENCES dbo.Offers (offer_id),
    CONSTRAINT FK_Bookings_Borrower FOREIGN KEY (borrower_id) REFERENCES dbo.Users (user_id)
);
GO

CREATE TABLE dbo.Reviews
(
    review_id INT IDENTITY(1,1) NOT NULL,
    booking_id INT NOT NULL,
    reviewer_id INT NOT NULL,
    reviewee_id INT NOT NULL,
    rating TINYINT NOT NULL,
    comment NVARCHAR(1000) NULL,
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Reviews_CreatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_Reviews PRIMARY KEY (review_id),
    CONSTRAINT UQ_Reviews_BookingReviewer UNIQUE (booking_id, reviewer_id),
    CONSTRAINT CK_Reviews_Rating CHECK (rating BETWEEN 1 AND 5),
    CONSTRAINT CK_Reviews_DifferentPeople CHECK (reviewer_id <> reviewee_id),
    CONSTRAINT FK_Reviews_Bookings FOREIGN KEY (booking_id) REFERENCES dbo.Bookings (booking_id),
    CONSTRAINT FK_Reviews_Reviewer FOREIGN KEY (reviewer_id) REFERENCES dbo.Users (user_id),
    CONSTRAINT FK_Reviews_Reviewee FOREIGN KEY (reviewee_id) REFERENCES dbo.Users (user_id)
);
GO

CREATE TABLE dbo.Notifications
(
    notification_id INT IDENTITY(1,1) NOT NULL,
    user_id INT NOT NULL,
    request_id INT NOT NULL,
    message NVARCHAR(1000) NOT NULL,
    notification_type NVARCHAR(50) NOT NULL,
    is_read BIT NOT NULL
        CONSTRAINT DF_Notifications_IsRead DEFAULT (0),
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_Notifications_CreatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_Notifications PRIMARY KEY (notification_id),
    CONSTRAINT FK_Notifications_Users FOREIGN KEY (user_id) REFERENCES dbo.Users (user_id),
    CONSTRAINT FK_Notifications_Requests FOREIGN KEY (request_id) REFERENCES dbo.Requests (request_id)
);
GO

CREATE TABLE dbo.AuditLogs
(
    log_id BIGINT IDENTITY(1,1) NOT NULL,
    user_id INT NULL,
    action NVARCHAR(100) NOT NULL,
    entity_type NVARCHAR(100) NOT NULL,
    entity_id NVARCHAR(100) NULL,
    ip_address NVARCHAR(45) NULL,
    created_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_AuditLogs_CreatedAt DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT PK_AuditLogs PRIMARY KEY (log_id),
    -- A null user_id permits system events and preserves logs if a user is later removed.
    CONSTRAINT FK_AuditLogs_Users FOREIGN KEY (user_id) REFERENCES dbo.Users (user_id)
);
GO

-- Nonclustered indexes support frequent filters and foreign-key joins.
-- UNIQUE constraints above already create indexes for Users.email, Users.phone,
-- Categories.category_name, Localities' natural key, and Bookings.offer_id.
CREATE NONCLUSTERED INDEX IX_Users_LocalityId ON dbo.Users (locality_id);
CREATE NONCLUSTERED INDEX IX_Items_OwnerId ON dbo.Items (owner_id);
CREATE NONCLUSTERED INDEX IX_Items_CategoryId ON dbo.Items (category_id);
CREATE NONCLUSTERED INDEX IX_Items_IsAvailable ON dbo.Items (is_available);
CREATE NONCLUSTERED INDEX IX_ItemImages_ItemId ON dbo.ItemImages (item_id);
CREATE NONCLUSTERED INDEX IX_Requests_RequesterId ON dbo.Requests (requester_id);
CREATE NONCLUSTERED INDEX IX_Requests_CategoryId ON dbo.Requests (category_id);
CREATE NONCLUSTERED INDEX IX_Requests_LocalityId ON dbo.Requests (locality_id);
CREATE NONCLUSTERED INDEX IX_Requests_StatusDates ON dbo.Requests (status, start_datetime, end_datetime);
CREATE NONCLUSTERED INDEX IX_Offers_RequestId ON dbo.Offers (request_id);
CREATE NONCLUSTERED INDEX IX_Offers_ItemId ON dbo.Offers (item_id);
CREATE NONCLUSTERED INDEX IX_Offers_OwnerId ON dbo.Offers (owner_id);
CREATE NONCLUSTERED INDEX IX_Offers_Status ON dbo.Offers (status);
CREATE NONCLUSTERED INDEX IX_Bookings_BorrowerId ON dbo.Bookings (borrower_id);
CREATE NONCLUSTERED INDEX IX_Bookings_StatusDates ON dbo.Bookings (status, start_datetime, end_datetime);
CREATE NONCLUSTERED INDEX IX_Reviews_RevieweeId ON dbo.Reviews (reviewee_id);
CREATE NONCLUSTERED INDEX IX_Notifications_UserReadCreated ON dbo.Notifications (user_id, is_read, created_at);
CREATE NONCLUSTERED INDEX IX_AuditLogs_UserCreated ON dbo.AuditLogs (user_id, created_at);
GO
