using System;
using System.Collections.Generic;
using System.Configuration;
using System.Data;
using System.Data.SqlClient;
using System.Linq;
using System.Transactions;
using Dapper;

namespace WWI.Legacy.Web.DataAccess
{
    public class OrderLineInput
    {
        public int OrderReference { get; set; }
        public int StockItemID { get; set; }
        public string Description { get; set; }
        public int Quantity { get; set; }
    }

    /// <summary>
    /// Dapper-based order data access, plus the table-valued-parameter call into WWI.
    /// </summary>
    public class OrderRepository
    {
        private readonly string _connectionString =
            ConfigurationManager.ConnectionStrings["WideWorldImporters"].ConnectionString;

        public IEnumerable<dynamic> GetOrdersForCustomer(int customerId)
        {
            using (var connection = new SqlConnection(_connectionString))
            {
                return connection.Query(
                    @"SELECT o.OrderID, o.OrderDate, o.ExpectedDeliveryDate,
                             (SELECT ol.StockItemID, ol.Quantity, ol.UnitPrice
                              FROM Sales.OrderLines AS ol
                              WHERE ol.OrderID = o.OrderID
                              FOR JSON PATH) AS LinesJson
                      FROM Sales.Orders AS o
                      WHERE o.CustomerID = @customerId
                      ORDER BY o.OrderDate DESC",
                    new { customerId });
            }
        }

        // Point-in-time view of a stock item, using the system-versioned history table.
        public dynamic GetStockItemAsOf(int stockItemId, DateTime asOf)
        {
            using (var connection = new SqlConnection(_connectionString))
            {
                return connection.QueryFirstOrDefault(
                    @"SELECT StockItemID, StockItemName, UnitPrice, RecommendedRetailPrice
                      FROM Warehouse.StockItems FOR SYSTEM_TIME AS OF @asOf
                      WHERE StockItemID = @stockItemId",
                    new { stockItemId, asOf });
            }
        }

        public void UpsertSpecialDeal(int stockItemId, decimal discountPercentage, int editedBy)
        {
            const string sql = @"
                MERGE Sales.SpecialDeals AS target
                USING (SELECT @stockItemId AS StockItemID) AS source
                   ON target.StockItemID = source.StockItemID
                WHEN MATCHED THEN
                    UPDATE SET DiscountPercentage = @discountPercentage, LastEditedBy = @editedBy,
                               LastEditedWhen = SYSDATETIME()
                WHEN NOT MATCHED THEN
                    INSERT (StockItemID, DealDescription, StartDate, EndDate, DiscountPercentage, LastEditedBy, LastEditedWhen)
                    VALUES (@stockItemId, N'Web deal', CAST(GETDATE() AS date), DATEADD(day, 30, CAST(GETDATE() AS date)),
                            @discountPercentage, @editedBy, SYSDATETIME())
                OUTPUT inserted.SpecialDealID;";

            using (var scope = new TransactionScope())
            using (var connection = new SqlConnection(_connectionString))
            {
                try
                {
                    connection.Execute(sql, new { stockItemId, discountPercentage, editedBy });
                    scope.Complete();
                }
                catch (SqlException ex) when (ex.Number == 2627 || ex.Number == 2601)
                {
                    throw new InvalidOperationException("Duplicate special deal", ex);
                }
            }
        }

        public void ImportColorsFromJson(string colorsJson, int editedBy)
        {
            using (var connection = new SqlConnection(_connectionString))
            {
                connection.Execute(
                    @"INSERT INTO Warehouse.Colors (ColorName, LastEditedBy)
                      SELECT j.ColorName, @editedBy
                      FROM OPENJSON(@colorsJson) WITH (ColorName nvarchar(20) '$.name') AS j
                      WHERE NOT EXISTS (SELECT 1 FROM Warehouse.Colors c WHERE c.ColorName = j.ColorName)",
                    new { colorsJson, editedBy });
            }
        }

        // Website.InsertCustomerOrders takes two table-valued parameters.
        public void InsertOrders(DataTable orders, IEnumerable<OrderLineInput> lines, int createdBy, int salesperson)
        {
            var lineTable = new DataTable();
            lineTable.Columns.Add("OrderReference", typeof(int));
            lineTable.Columns.Add("StockItemID", typeof(int));
            lineTable.Columns.Add("Description", typeof(string));
            lineTable.Columns.Add("Quantity", typeof(int));
            foreach (var line in lines)
            {
                lineTable.Rows.Add(line.OrderReference, line.StockItemID, line.Description, line.Quantity);
            }

            using (var connection = new SqlConnection(_connectionString))
            using (var command = new SqlCommand("Website.InsertCustomerOrders", connection))
            {
                command.CommandType = CommandType.StoredProcedure;
                var ordersParam = command.Parameters.AddWithValue("@Orders", orders);
                ordersParam.SqlDbType = SqlDbType.Structured;
                ordersParam.TypeName = "Website.OrderList";
                var linesParam = command.Parameters.AddWithValue("@OrderLines", lineTable);
                linesParam.SqlDbType = SqlDbType.Structured;
                linesParam.TypeName = "Website.OrderLineList";
                command.Parameters.AddWithValue("@OrdersCreatedByPersonID", createdBy);
                command.Parameters.AddWithValue("@SalespersonPersonID", salesperson);
                connection.Open();
                command.ExecuteNonQuery();
            }
        }

        public IEnumerable<dynamic> GetCityUpdates(DateTime lastCutoff, DateTime newCutoff)
        {
            using (var connection = new SqlConnection(_connectionString))
            {
                return connection.Query(
                    "Integration.GetCityUpdates",
                    new { LastCutoff = lastCutoff, NewCutoff = newCutoff },
                    commandType: CommandType.StoredProcedure);
            }
        }

        public int RecordTemperatures(string sensorJson)
        {
            using (var connection = new SqlConnection(_connectionString))
            {
                return connection.Execute("EXEC Website.RecordVehicleTemperature @FullSensorDataArray = @json",
                    new { json = sensorJson });
            }
        }

        public void CallRetiredProcedure()
        {
            using (var connection = new SqlConnection(_connectionString))
            {
                // This procedure was removed from the database in 2019 but the call was never deleted.
                connection.Execute("Sales.RecalculateCustomerBalances", commandType: CommandType.StoredProcedure);
            }
        }
    }
}
