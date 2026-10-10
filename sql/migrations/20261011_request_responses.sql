-- Track each locality user's response to a request so ignored requests stay hidden
-- from that user without affecting other users or request availability.
IF OBJECT_ID(N'dbo.RequestResponses', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.RequestResponses
    (
        response_id INT IDENTITY(1,1) NOT NULL,
        request_id INT NOT NULL,
        user_id INT NOT NULL,
        response_status NVARCHAR(12) NOT NULL,
        created_at DATETIME2(0) NOT NULL
            CONSTRAINT DF_RequestResponses_CreatedAt DEFAULT (SYSUTCDATETIME()),
        updated_at DATETIME2(0) NOT NULL
            CONSTRAINT DF_RequestResponses_UpdatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_RequestResponses PRIMARY KEY (response_id),
        CONSTRAINT UQ_RequestResponses_RequestUser UNIQUE (request_id, user_id),
        CONSTRAINT CK_RequestResponses_Status CHECK
            (response_status IN (N'INTERESTED', N'IGNORED', N'OFFERED')),
        CONSTRAINT FK_RequestResponses_Requests FOREIGN KEY (request_id)
            REFERENCES dbo.Requests (request_id),
        CONSTRAINT FK_RequestResponses_Users FOREIGN KEY (user_id)
            REFERENCES dbo.Users (user_id)
    );
END;
GO

IF NOT EXISTS
    (SELECT 1 FROM sys.indexes
     WHERE name = N'IX_RequestResponses_UserStatus'
       AND object_id = OBJECT_ID(N'dbo.RequestResponses'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_RequestResponses_UserStatus
        ON dbo.RequestResponses (user_id, response_status, request_id);
END;
GO
