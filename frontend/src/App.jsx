import { Routes, Route, Link } from "react-router-dom";
import ProtectedRoute from "./routes/ProtectedRoute.jsx";
import AuthPage from "./pages/AuthPage.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import ClassesPage from "./pages/ClassesPage.jsx";
import AssignmentsPage from "./pages/AssignmentsPage.jsx";

export default function App() {
  return (
    <>
      <nav style={{ padding: 12, borderBottom: "1px solid #eee" }}>
        <Link to="/" style={{ marginRight: 12 }}>Dashboard</Link>
        <Link to="/classes" style={{ marginRight: 12 }}>Classes</Link>
        <Link to="/assignments">Assignments</Link>
        <Link to="/auth" style={{ float: "right" }}>Auth</Link>
      </nav>
      <Routes>
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
        <Route path="/classes" element={<ProtectedRoute><ClassesPage /></ProtectedRoute>} />
        <Route path="/assignments" element={<ProtectedRoute><AssignmentsPage /></ProtectedRoute>} />
      </Routes>
    </>
  );
}
