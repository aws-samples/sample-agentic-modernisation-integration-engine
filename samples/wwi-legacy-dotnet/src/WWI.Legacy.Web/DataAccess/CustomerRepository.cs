using System;
using System.Collections.Generic;
using System.Configuration;
using System.Data;
using System.Data.SqlClient;

namespace WWI.Legacy.Web.DataAccess
{
    public class CustomerSummary
    {
        public int CustomerID { get; set; }
        public string CustomerName { get; set; }
        public string CityName { get; set; }
        public string PhoneNumber { get; set; }
    }

    /// <summary>
    /// Classic ADO.NET repository: stored procedures plus hand-written T-SQL.
    /// </summary>
    public class CustomerRepository
    {
        private readonly string _connectionString =
            ConfigurationManager.ConnectionStrings["WideWorldImporters"].ConnectionString;

        // Calls Website.SearchForCustomers (returns FOR JSON in WWI).
        public string SearchCustomers(string searchText, int maxRows)
        {
            using (var connection = new SqlConnection(_connectionString))
            using (var command = new SqlCommand("Website.SearchForCustomers", connection))
            {
                command.CommandType = CommandType.StoredProcedure;
                command.Parameters.Add("@SearchText", SqlDbType.NVarChar, 1000).Value = searchText;
                command.Parameters.Add("@MaximumRowsToReturn", SqlDbType.Int).Value = maxRows;
                connection.Open();
                return (string)command.ExecuteScalar();
            }
        }

        // Calls Website.ActivateWebsiteLogon; reads the procedure's RETURN value.
        public int ActivateLogon(int personId, string logonName, string initialPassword)
        {
            using (var connection = new SqlConnection(_connectionString))
            using (var command = new SqlCommand("[Website].[ActivateWebsiteLogon]", connection))
            {
                command.CommandType = CommandType.StoredProcedure;
                command.Parameters.AddWithValue("@PersonID", personId);
                command.Parameters.AddWithValue("@LogonName", logonName);
                command.Parameters.AddWithValue("@InitialPassword", initialPassword);
                var returnValue = command.Parameters.Add("@ReturnValue", SqlDbType.Int);
                returnValue.Direction = ParameterDirection.ReturnValue;
                connection.Open();
                command.ExecuteNonQuery();
                return (int)returnValue.Value;
            }
        }

        public List<CustomerSummary> GetTopCustomersByCity(string cityName)
        {
            const string sql = @"
                SELECT TOP 50 c.CustomerID, c.CustomerName, ct.CityName, ISNULL(c.PhoneNumber, N'') AS PhoneNumber
                FROM Sales.Customers AS c WITH (NOLOCK)
                INNER JOIN [Application].Cities AS ct WITH (NOLOCK) ON ct.CityID = c.DeliveryCityID
                WHERE ct.CityName = @CityName
                  AND c.AccountOpenedDate <= CONVERT(date, GETDATE())
                ORDER BY c.CustomerName";

            var result = new List<CustomerSummary>();
            using (var connection = new SqlConnection(_connectionString))
            using (var command = new SqlCommand(sql, connection))
            {
                command.Parameters.AddWithValue("@CityName", cityName);
                connection.Open();
                using (var reader = command.ExecuteReader())
                {
                    while (reader.Read())
                    {
                        result.Add(new CustomerSummary
                        {
                            CustomerID = reader.GetInt32(0),
                            CustomerName = reader.GetString(1),
                            CityName = reader.GetString(2),
                            PhoneNumber = reader.GetString(3)
                        });
                    }
                }
            }
            return result;
        }

        // Legacy search screen: builds SQL by concatenation (SQL injection risk).
        public DataTable SearchByName(string namePrefix, string sortColumn)
        {
            var sql = "SELECT TOP 100 CustomerID, CustomerName, PhoneNumber FROM Sales.Customers " +
                      "WHERE CustomerName LIKE '" + namePrefix + "%' ORDER BY " + sortColumn;
            var table = new DataTable();
            using (var adapter = new SqlDataAdapter(sql, _connectionString))
            {
                adapter.Fill(table);
            }
            return table;
        }

        public int CreateBuyingGroup(string name, int editedBy)
        {
            using (var connection = new SqlConnection(_connectionString))
            using (var command = new SqlCommand(
                "INSERT INTO Sales.BuyingGroups (BuyingGroupName, LastEditedBy) VALUES (@Name, @EditedBy); " +
                "SELECT CAST(SCOPE_IDENTITY() AS int);", connection))
            {
                command.Parameters.AddWithValue("@Name", name);
                command.Parameters.AddWithValue("@EditedBy", editedBy);
                connection.Open();
                return (int)command.ExecuteScalar();
            }
        }

        public DataTable GetCustomerTransactions(int customerId, DateTime from)
        {
            var sql = $"SELECT CustomerTransactionID, TransactionDate, TransactionAmount " +
                      $"FROM Sales.CustomerTransactions WHERE CustomerID = {customerId} " +
                      $"AND TransactionDate >= '{from:yyyy-MM-dd}' ORDER BY TransactionDate DESC " +
                      "OFFSET 0 ROWS FETCH NEXT 200 ROWS ONLY";
            var table = new DataTable();
            using (var adapter = new SqlDataAdapter(sql, _connectionString))
            {
                adapter.Fill(table);
            }
            return table;
        }
    }
}
