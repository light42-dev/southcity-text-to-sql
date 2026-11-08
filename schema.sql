-- DUMMY SCHEMA FOR AST PROPERTY PLUS ERP TEST (SOUTHCITY)

-- ============================================================================
-- SOUTHCITY ERP DATABASE BENCHMARK (TEXT-TO-SQL V2.2)
-- Dialect: ANSI SQL / Compatible with MS SQL Server (2016+), Azure SQL & PostgreSQL
-- Standar Denda: 1 Per Mil (0.1% / 0.0010 per hari keterlambatan)
-- Fitur: Enterprise RBAC Table + Prompt Injection & Security Defense Suite
-- ============================================================================

-- DROP TABLES (Urutan drop dari child ke parent untuk menghindari foreign key conflict)
DROP TABLE IF EXISTS Late_Fee_Penalties;
DROP TABLE IF EXISTS Payments_Received;
DROP TABLE IF EXISTS Payment_Schedules;
DROP TABLE IF EXISTS Sales_Contracts;
DROP TABLE IF EXISTS Units;
DROP TABLE IF EXISTS System_Users;

-- 0. SECURITY & RBAC TABLE: SYSTEM_USERS
CREATE TABLE System_Users (
    UserID INT PRIMARY KEY,
    Username VARCHAR(50) NOT NULL UNIQUE,
    FullName VARCHAR(100) NOT NULL,
    Role VARCHAR(30) NOT NULL,               -- 'Sales', 'Finance', 'Auditor', 'SuperAdmin'
    AssignedAgentID INT NULL,                -- Mapping ke SalesAgentID jika Role = 'Sales'
    IsActive BIT NOT NULL DEFAULT 1          -- Status aktif akun (1: Active, 0: Inactive)
);

-- 1. MASTER TABLE: UNITS
CREATE TABLE Units (
    UnitID INT PRIMARY KEY,
    UnitNumber VARCHAR(20) NOT NULL UNIQUE,
    Tower VARCHAR(50) NOT NULL,              -- e.g. 'Tower A', 'Tower B'
    UnitType VARCHAR(20) NOT NULL,           -- e.g. 'Studio', '2BR', '3BR'
    Floor INT NOT NULL,
    SGA_Area DECIMAL(8, 2) NOT NULL,         -- Semi Gross Area (m2)
    Nett_Area DECIMAL(8, 2) NOT NULL,        -- Luas Bersih (m2)
    Status VARCHAR(20) NOT NULL DEFAULT 'Vacant' -- 'Vacant', 'Reserved', 'Sold'
);

-- 2. TRANSACTION TABLE: SALES_CONTRACTS
CREATE TABLE Sales_Contracts (
    ContractID INT PRIMARY KEY,
    ContractNumber VARCHAR(50) NOT NULL UNIQUE,
    UnitID INT NOT NULL UNIQUE,              -- 1 Unit aktif hanya berelasi dengan 1 Kontrak aktif
    CustomerName VARCHAR(150) NOT NULL,
    CustomerNIK VARCHAR(20) NOT NULL,
    SalesAgentID INT NOT NULL,               -- ID Sales In-House (digunakan untuk isolasi data RBAC)
    BookingDate DATE NOT NULL,
    BasePrice DECIMAL(15, 2) NOT NULL,       -- Harga dasar pengembang sebelum PPN
    VAT_Amount DECIMAL(15, 2) NOT NULL,      -- PPN 11%
    TotalPrice DECIMAL(15, 2) NOT NULL,      -- BasePrice + VAT_Amount
    PaymentScheme VARCHAR(30) NOT NULL,      -- 'Hard Cash', 'Installment 24x', 'KPR Express'
    ContractStatus VARCHAR(20) NOT NULL DEFAULT 'Active', -- 'Active', 'Cancelled', 'Completed'
    FOREIGN KEY (UnitID) REFERENCES Units(UnitID)
);

-- 3. BILLING SCHEDULE: PAYMENT_SCHEDULES (Jadwal Termin Pembayaran)
CREATE TABLE Payment_Schedules (
    ScheduleID INT PRIMARY KEY,
    ContractID INT NOT NULL,
    InstallmentNo INT NOT NULL,              -- Cicilan ke-1, 2, 3, dst.
    DueDate DATE NOT NULL,
    BillingAmount DECIMAL(15, 2) NOT NULL,   -- Nominal tagihan termin ini
    BillingType VARCHAR(30) NOT NULL,        -- 'Booking Fee', 'Down Payment', 'Monthly Installment', 'Bank Disbursement'
    Status VARCHAR(20) NOT NULL DEFAULT 'Unpaid', -- 'Unpaid', 'Partially Paid', 'Paid'
    FOREIGN KEY (ContractID) REFERENCES Sales_Contracts(ContractID)
);

-- 4. CASH INFLOW: PAYMENTS_RECEIVED (Realisasi Pembayaran Kas Masuk / Kwitansi)
CREATE TABLE Payments_Received (
    PaymentID INT PRIMARY KEY,
    ScheduleID INT NOT NULL,
    ContractID INT NOT NULL,                 -- Denormalisasi untuk mempermudah agregasi langsung per kontrak
    PaymentDate DATE NOT NULL,
    PaidAmount DECIMAL(15, 2) NOT NULL,      -- Nilai nominal uang yang masuk
    PaymentMethod VARCHAR(50) NOT NULL,      -- 'Virtual Account BCA', 'Bank Transfer Mandiri', 'Cheque/Giro'
    ReceiptNumber VARCHAR(50) NOT NULL UNIQUE,
    BankReferenceNo VARCHAR(100),
    FOREIGN KEY (ScheduleID) REFERENCES Payment_Schedules(ScheduleID),
    FOREIGN KEY (ContractID) REFERENCES Sales_Contracts(ContractID)
);

-- 5. PENALTY / INTEREST: LATE_FEE_PENALTIES (Denda Keterlambatan Termin - Standar 1/Mil)
CREATE TABLE Late_Fee_Penalties (
    PenaltyID INT PRIMARY KEY,
    ScheduleID INT NOT NULL,
    ContractID INT NOT NULL,
    CalculationDate DATE NOT NULL,               -- Tanggal sistem menjalankan kalkulasi denda
    BaseOverdueAmount DECIMAL(15, 2) NOT NULL,   -- Sisa pokok yang tertunggak saat DueDate terlampaui
    DaysOverdue INT NOT NULL,                    -- Jumlah hari keterlambatan kalender dari DueDate
    DailyRatePermil DECIMAL(5, 4) NOT NULL DEFAULT 0.0010, -- Standar 1/mil per hari (0.1% atau 0.0010)
    PenaltyAmount DECIMAL(15, 2) NOT NULL,       -- BaseOverdueAmount * DaysOverdue * 0.0010
    WaiverAmount DECIMAL(15, 2) DEFAULT 0,       -- Keringanan/diskon denda disetujui manajemen
    PaidPenalty DECIMAL(15, 2) DEFAULT 0,        -- Denda yang sudah dicicil/dilunasi customer
    PenaltyStatus VARCHAR(20) NOT NULL DEFAULT 'Outstanding', -- 'Outstanding', 'Settled', 'Waived'
    FOREIGN KEY (ScheduleID) REFERENCES Payment_Schedules(ScheduleID),
    FOREIGN KEY (ContractID) REFERENCES Sales_Contracts(ContractID)
);

-- ============================================================================
-- INDEXES UNTUK EFISIENSI QUERY & OPTIMASI TEXT-TO-SQL
-- ============================================================================
CREATE INDEX idx_users_role ON System_Users(Username, Role);
CREATE INDEX idx_units_status ON Units(Status, Tower);
CREATE INDEX idx_contracts_agent ON Sales_Contracts(SalesAgentID);
CREATE INDEX idx_schedules_due ON Payment_Schedules(DueDate, Status);
CREATE INDEX idx_payments_contract ON Payments_Received(ContractID, PaymentDate);
CREATE INDEX idx_penalties_contract ON Late_Fee_Penalties(ContractID, PenaltyStatus);

-- ============================================================================
-- SEED DATA (EXPANDED PRODUCTION-LIKE BENCHMARK)
-- ============================================================================

-- 0. SYSTEM USERS (Multi-user RBAC Context)
INSERT INTO System_Users (UserID, Username, FullName, Role, AssignedAgentID, IsActive) VALUES 
(1, 'sales_bambang', 'Bambang Pamungkas', 'Sales', 101, 1),
(2, 'sales_dewi', 'Dewi Lestari', 'Sales', 102, 1),
(3, 'sales_reza', 'Reza Rahadian', 'Sales', 103, 1),
(4, 'finance_hendra', 'Hendra Setiawan', 'Finance', NULL, 1),
(5, 'auditor_maya', 'Maya Kusuma', 'Auditor', NULL, 1);

-- 1. MASTER UNITS (12 Units: Beragam tipe, lantai, dan status)
INSERT INTO Units (UnitID, UnitNumber, Tower, UnitType, Floor, SGA_Area, Nett_Area, Status) VALUES 
(1,  'A-0101', 'Tower A', 'Studio', 1,  24.50, 20.00, 'Sold'),
(2,  'A-0502', 'Tower A', '2BR',    5,  48.00, 41.50, 'Vacant'),
(3,  'A-1205', 'Tower A', '2BR',    12, 52.00, 45.00, 'Sold'),
(4,  'A-1508', 'Tower A', '3BR',    15, 85.00, 74.00, 'Sold'),
(5,  'A-2001', 'Tower A', 'Studio', 20, 24.50, 20.00, 'Vacant'),
(6,  'B-0301', 'Tower B', 'Studio', 3,  24.50, 20.00, 'Vacant'),
(7,  'B-0703', 'Tower B', '2BR',    7,  50.00, 43.00, 'Vacant'),
(8,  'B-1008', 'Tower B', '3BR',    10, 85.00, 74.00, 'Reserved'),
(9,  'B-1602', 'Tower B', '2BR',    16, 52.00, 45.00, 'Sold'),
(10, 'B-1805', 'Tower B', 'Studio', 18, 25.00, 20.50, 'Sold'),
(11, 'B-2101', 'Tower B', '3BR',    21, 92.00, 80.00, 'Sold'),
(12, 'B-2202', 'Tower B', '2BR',    22, 52.00, 45.00, 'Vacant');

-- 2. SALES CONTRACTS (6 Kontrak: Hard Cash, Installment 24x, KPR Express)
-- Kontrak 101: Budi Santoso (Agent 101 / Bambang) - Installment 24x
-- Kontrak 102: Siti Rahma (Agent 102 / Dewi) - KPR Express
-- Kontrak 103: Robert Tan (Agent 101 / Bambang) - Hard Cash (Lunas)
-- Kontrak 104: Linda Wijaya (Agent 103 / Reza) - Installment 24x (Denda Berat)
-- Kontrak 105: Ahmad Farhan (Agent 102 / Dewi) - KPR Express (Nunggak DP)
-- Kontrak 106: Christine Hartono (Agent 103 / Reza) - Installment 24x (Denda Lunas / Settled)
INSERT INTO Sales_Contracts (ContractID, ContractNumber, UnitID, CustomerName, CustomerNIK, SalesAgentID, BookingDate, BasePrice, VAT_Amount, TotalPrice, PaymentScheme, ContractStatus) VALUES 
(101, 'CTR-SC-2026-001', 1,  'Budi Santoso',      '3171012345670001', 101, '2026-01-10', 900000000.00,   99000000.00,  999000000.00,  'Installment 24x', 'Active'),
(102, 'CTR-SC-2026-002', 3,  'Siti Rahma',        '3171098765430002', 102, '2026-02-15', 1400000000.00, 154000000.00, 1554000000.00, 'KPR Express',     'Active'),
(103, 'CTR-SC-2026-003', 4,  'Robert Tan',        '3171055544430003', 101, '2026-01-20', 2100000000.00, 231000000.00, 2331000000.00, 'Hard Cash',       'Completed'),
(104, 'CTR-SC-2026-004', 9,  'Linda Wijaya',      '3275088899910004', 103, '2026-02-01', 1350000000.00, 148500000.00, 1498500000.00, 'Installment 24x', 'Active'),
(105, 'CTR-SC-2026-005', 10, 'Ahmad Farhan',      '3174066611120005', 102, '2026-03-05', 650000000.00,   71500000.00,  721500000.00,  'KPR Express',     'Active'),
(106, 'CTR-SC-2026-006', 11, 'Christine Hartono', '3173022233340006', 103, '2026-01-15', 2300000000.00, 253000000.00, 2553000000.00, 'Installment 24x', 'Active');

-- 3. PAYMENT SCHEDULES (Termin Tagihan)
INSERT INTO Payment_Schedules (ScheduleID, ContractID, InstallmentNo, DueDate, BillingAmount, BillingType, Status) VALUES 
-- Kontrak 101 (Budi Santoso - Installment 24x)
(1001, 101, 1, '2026-02-10', 50000000.00,  'Booking Fee',          'Paid'),
(1002, 101, 2, '2026-03-10', 40000000.00,  'Monthly Installment',   'Partially Paid'),
(1003, 101, 3, '2026-04-10', 40000000.00,  'Monthly Installment',   'Unpaid'),

-- Kontrak 102 (Siti Rahma - KPR Express: Pencairan KPR Bank di masa depan 2027)
(1005, 102, 1, '2026-02-20', 100000000.00, 'Down Payment',         'Paid'),
(1006, 102, 2, '2027-12-20', 1454000000.00,'Bank Disbursement',    'Unpaid'),

-- Kontrak 103 (Robert Tan - Hard Cash)
(1007, 103, 1, '2026-01-25', 50000000.00,  'Booking Fee',          'Paid'),
(1008, 103, 2, '2026-02-25', 2281000000.00,'Hard Cash Settlement', 'Paid'),

-- Kontrak 104 (Linda Wijaya - Installment 24x: Nunggak 2 termin berturut-turut)
(1009, 104, 1, '2026-02-15', 50000000.00,  'Booking Fee',          'Paid'),
(1010, 104, 2, '2026-03-15', 60000000.00,  'Monthly Installment',   'Unpaid'),
(1011, 104, 3, '2026-04-15', 60000000.00,  'Monthly Installment',   'Unpaid'),

-- Kontrak 105 (Ahmad Farhan - KPR Express: DP 2x, DP 2 telat bayar)
(1012, 105, 1, '2026-03-10', 30000000.00,  'Booking Fee',          'Paid'),
(1013, 105, 2, '2026-04-10', 50000000.00,  'Down Payment',         'Partially Paid'),
(1014, 105, 3, '2027-12-10', 641500000.00, 'Bank Disbursement',    'Unpaid'),

-- Kontrak 106 (Christine Hartono - Installment 24x: Denda sudah dilunasi)
(1015, 106, 1, '2026-01-20', 50000000.00,  'Booking Fee',          'Paid'),
(1016, 106, 2, '2026-02-20', 100000000.00, 'Down Payment',         'Paid'),
(1017, 106, 3, '2026-03-20', 100000000.00, 'Monthly Installment',   'Paid');

-- 4. PAYMENTS RECEIVED (Realisasi Kas Masuk)
INSERT INTO Payments_Received (PaymentID, ScheduleID, ContractID, PaymentDate, PaidAmount, PaymentMethod, ReceiptNumber, BankReferenceNo) VALUES 
-- Budi Santoso (101)
(501, 1001, 101, '2026-02-09', 50000000.00,   'Virtual Account BCA',   'REC-202602-001', 'BCAVA-882910'),
(502, 1002, 101, '2026-03-15', 15000000.00,   'Virtual Account BCA',   'REC-202603-044', 'BCAVA-882911'),

-- Siti Rahma (102)
(503, 1005, 102, '2026-02-19', 100000000.00,  'Bank Transfer Mandiri', 'REC-202602-019', 'MND-992100'),

-- Robert Tan (103 - Lunas)
(504, 1007, 103, '2026-01-24', 50000000.00,   'Bank Transfer Mandiri', 'REC-202601-008', 'MND-881230'),
(505, 1008, 103, '2026-02-24', 2281000000.00, 'Cheque/Giro',          'REC-202602-099', 'GIRO-BCA-00129'),

-- Linda Wijaya (104 - Baru bayar booking fee)
(506, 1009, 104, '2026-02-14', 50000000.00,   'Virtual Account BCA',   'REC-202602-025', 'BCAVA-883301'),

-- Ahmad Farhan (105 - Booking fee lunas + DP cicil 20jt)
(507, 1012, 105, '2026-03-09', 30000000.00,   'Virtual Account BCA',   'REC-202603-011', 'BCAVA-884100'),
(508, 1013, 105, '2026-04-18', 20000000.00,   'Virtual Account BCA',   'REC-202604-032', 'BCAVA-884101'),

-- Christine Hartono (106 - Lunas sampai termin 3)
(509, 1015, 106, '2026-01-19', 50000000.00,   'Bank Transfer Mandiri', 'REC-202601-005', 'MND-771120'),
(510, 1016, 106, '2026-02-19', 100000000.00,  'Bank Transfer Mandiri', 'REC-202602-015', 'MND-771125'),
(511, 1017, 106, '2026-04-05', 100000000.00,  'Virtual Account BCA',   'REC-202604-002', 'BCAVA-885501');

-- 5. LATE FEE PENALTIES (Perhitungan Denda Standar 1/mil per Hari)
-- Kasus 1 (9001 - Budi Santoso): Nunggak pokok 25jt, telat 30 hari -> Denda 750rb, Waiver 150rb -> Net Outstanding: 600rb
-- Kasus 2 (9002 - Linda Wijaya): Termin 2 nunggak full 60jt, telat 45 hari -> Denda: 60jt * 45 * 0.0010 = 2.700.000 (Outstanding)
-- Kasus 3 (9003 - Linda Wijaya): Termin 3 nunggak full 60jt, telat 15 hari -> Denda: 60jt * 15 * 0.0010 = 900.000 (Outstanding)
-- Kasus 4 (9004 - Ahmad Farhan): Termin 2 nunggak sisa 30jt, telat 20 hari -> Denda: 30jt * 20 * 0.0010 = 600.000 (Outstanding)
-- Kasus 5 (9005 - Christine Hartono): Dulu telat termin 3 (100jt telat 16 hari = 1.600.000), sudah bayar denda full (PaidPenalty = 1.6jt) -> Settled
INSERT INTO Late_Fee_Penalties (PenaltyID, ScheduleID, ContractID, CalculationDate, BaseOverdueAmount, DaysOverdue, DailyRatePermil, PenaltyAmount, WaiverAmount, PaidPenalty, PenaltyStatus) VALUES 
(9001, 1002, 101, '2026-04-10', 25000000.00, 30, 0.0010, 750000.00,  150000.00, 0.00,       'Outstanding'),
(9002, 1010, 104, '2026-04-30', 60000000.00, 45, 0.0010, 2700000.00, 0.00,      0.00,       'Outstanding'),
(9003, 1011, 104, '2026-04-30', 60000000.00, 15, 0.0010, 900000.00,  0.00,      0.00,       'Outstanding'),
(9004, 1013, 105, '2026-04-30', 30000000.00, 20, 0.0010, 600000.00,  0.00,      0.00,       'Outstanding'),
(9005, 1017, 106, '2026-04-05', 100000000.00,16, 0.0010, 1600000.00, 0.00,      1600000.00, 'Settled');

