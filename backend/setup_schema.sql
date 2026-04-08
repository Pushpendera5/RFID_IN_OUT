SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;

/* inventory table */
IF OBJECT_ID(N'dbo.inventory', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.inventory (
        tag_id NVARCHAR(50) NOT NULL,
        item_name NVARCHAR(255) NULL,
        category NVARCHAR(100) NULL,
        metal_type NVARCHAR(50) NULL,
        purity NVARCHAR(50) NULL,
        [weight] FLOAT NULL,
        huid NVARCHAR(50) NULL,
        piece FLOAT NULL,
        [timestamp] NVARCHAR(100) NULL,
        CONSTRAINT PK_inventory PRIMARY KEY CLUSTERED (tag_id)
    );
END;

/* scan_logs table */
IF OBJECT_ID(N'dbo.scan_logs', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.scan_logs (
        id INT IDENTITY(1,1) NOT NULL,
        tag_id NVARCHAR(50) NULL,
        item_name NVARCHAR(255) NULL,
        category NVARCHAR(100) NULL,
        [weight] FLOAT NULL,
        piece FLOAT NULL,
        huid NVARCHAR(50) NULL,
        direction NVARCHAR(10) NULL,
        [timestamp] NVARCHAR(50) NULL,
        [date] NVARCHAR(50) NULL,
        CONSTRAINT PK_scan_logs PRIMARY KEY CLUSTERED (id)
    );
END;

/* users table */
IF OBJECT_ID(N'dbo.users', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.users (
        id INT IDENTITY(1,1) NOT NULL,
        username NVARCHAR(100) NOT NULL,
        [password] NVARCHAR(255) NOT NULL,
        [role] NVARCHAR(20) NOT NULL CONSTRAINT DF_users_role DEFAULT ('staff'),
        CONSTRAINT PK_users PRIMARY KEY CLUSTERED (id)
    );
END;

/* unique username constraint/index */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'UX_users_username'
      AND object_id = OBJECT_ID(N'dbo.users')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX UX_users_username
        ON dbo.users(username);
END;

/* backward-compat: ensure role exists in old users table */
IF OBJECT_ID(N'dbo.users', N'U') IS NOT NULL
   AND COL_LENGTH('dbo.users', 'role') IS NULL
BEGIN
    ALTER TABLE dbo.users
    ADD [role] NVARCHAR(20) NOT NULL
        CONSTRAINT DF_users_role_legacy DEFAULT ('staff') WITH VALUES;
END;

/* backward-compat: ensure piece exists and copy old price data */
IF OBJECT_ID(N'dbo.inventory', N'U') IS NOT NULL
   AND COL_LENGTH('dbo.inventory', 'piece') IS NULL
BEGIN
    ALTER TABLE dbo.inventory ADD piece FLOAT NULL;
END;

IF OBJECT_ID(N'dbo.scan_logs', N'U') IS NOT NULL
   AND COL_LENGTH('dbo.scan_logs', 'piece') IS NULL
BEGIN
    ALTER TABLE dbo.scan_logs ADD piece FLOAT NULL;
END;

IF OBJECT_ID(N'dbo.inventory', N'U') IS NOT NULL
   AND COL_LENGTH('dbo.inventory', 'price') IS NOT NULL
BEGIN
    UPDATE dbo.inventory
    SET piece = price
    WHERE piece IS NULL AND price IS NOT NULL;
END;

IF OBJECT_ID(N'dbo.scan_logs', N'U') IS NOT NULL
   AND COL_LENGTH('dbo.scan_logs', 'price') IS NOT NULL
BEGIN
    UPDATE dbo.scan_logs
    SET piece = price
    WHERE piece IS NULL AND price IS NOT NULL;
END;

PRINT 'Schema setup completed successfully.';
