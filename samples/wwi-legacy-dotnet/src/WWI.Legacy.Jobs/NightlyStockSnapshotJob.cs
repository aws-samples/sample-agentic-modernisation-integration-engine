using System;
using System.Configuration;
using System.Data;
using System.Data.SqlClient;
using System.IO;

namespace WWI.Legacy.Jobs
{
    /// <summary>
    /// Console batch job started by SQL Server Agent (CmdExec step) every night.
    /// Copies stock holdings into a snapshot table and writes a CSV to a Windows file share.
    /// </summary>
    public static class NightlyStockSnapshotJob
    {
        private const string ExportFolder = @"\\wwi-files01\exports\stock";

        public static int Main(string[] args)
        {
            var connectionString = ConfigurationManager.ConnectionStrings["WideWorldImporters"].ConnectionString;
            try
            {
                using (var connection = new SqlConnection(connectionString))
                {
                    connection.Open();

                    var holdings = new DataTable();
                    using (var command = new SqlCommand(
                        @"SELECT StockItemID, QuantityOnHand, BinLocation, LastStocktakeQuantity, GETDATE() AS SnapshotTime
                          FROM Warehouse.StockItemHoldings WITH (NOLOCK)", connection))
                    using (var adapter = new SqlDataAdapter(command))
                    {
                        adapter.Fill(holdings);
                    }

                    using (var create = new SqlCommand(
                        @"IF OBJECT_ID('tempdb..#Snapshot') IS NOT NULL DROP TABLE #Snapshot;
                          CREATE TABLE #Snapshot (StockItemID int, QuantityOnHand int, BinLocation nvarchar(20),
                                                  LastStocktakeQuantity int, SnapshotTime datetime2(7));", connection))
                    {
                        create.ExecuteNonQuery();
                    }

                    using (var bulk = new SqlBulkCopy(connection))
                    {
                        bulk.DestinationTableName = "#Snapshot";
                        bulk.BatchSize = 5000;
                        bulk.WriteToServer(holdings);
                    }

                    using (var archive = new SqlCommand(
                        @"INSERT INTO Warehouse.StockItemHoldingsSnapshot
                          SELECT s.*, NEWID() FROM #Snapshot AS s;
                          SELECT @@ROWCOUNT;", connection))
                    {
                        archive.CommandTimeout = 600;
                        var rows = (int)archive.ExecuteScalar();
                        Console.WriteLine("Archived {0} rows", rows);
                    }

                    var file = Path.Combine(ExportFolder, $"stock-{DateTime.Now:yyyyMMdd}.csv");
                    using (var writer = new StreamWriter(file))
                    {
                        foreach (DataRow row in holdings.Rows)
                        {
                            writer.WriteLine(string.Join(",", row.ItemArray));
                        }
                    }
                }
                return 0;
            }
            catch (SqlException ex) when (ex.Number == 1205)
            {
                Console.Error.WriteLine("Deadlock, the Agent job will retry: " + ex.Message);
                return 2;
            }
        }
    }
}
